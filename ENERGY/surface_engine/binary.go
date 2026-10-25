// CES3 binary transport. Explicit little endian schema; legacy JSON is unchanged.
package main

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"io"
	"math"
	"os"
	"runtime"
	"sync"
	"time"
)

const maxFrame = 256 << 20

var le = binary.LittleEndian

type reader struct {
	b   []byte
	p   int
	err error
}

func (r *reader) take(n int) []byte {
	if r.err != nil {
		return make([]byte, n)
	}
	if n < 0 || n > len(r.b)-r.p {
		r.err = fmt.Errorf("truncated binary payload")
		return make([]byte, n)
	}
	b := r.b[r.p : r.p+n]
	r.p += n
	return b
}
func (r *reader) u() uint32  { return le.Uint32(r.take(4)) }
func (r *reader) id() uint64 { return le.Uint64(r.take(8)) }
func (r *reader) f() float64 { return math.Float64frombits(le.Uint64(r.take(8))) }
func (r *reader) array() []float64 {
	n := int(r.u())
	if n > (len(r.b)-r.p)/8 {
		r.err = fmt.Errorf("invalid array length")
		return nil
	}
	a := make([]float64, n)
	for i := range a {
		a[i] = r.f()
	}
	return a
}
func putU(b *bytes.Buffer, v uint32)  { var a [4]byte; le.PutUint32(a[:], v); b.Write(a[:]) }
func putID(b *bytes.Buffer, v uint64) { var a [8]byte; le.PutUint64(a[:], v); b.Write(a[:]) }
func putF(b *bytes.Buffer, v float64) {
	var a [8]byte
	le.PutUint64(a[:], math.Float64bits(v))
	b.Write(a[:])
}
func putArray(b *bytes.Buffer, a []float64) {
	putU(b, uint32(len(a)))
	for _, v := range a {
		putF(b, v)
	}
}
func readHour(r *reader) Hour {
	return Hour{Air: r.f(), Ground: r.f(), SkyIR: r.f(), Internal: r.f(), Heat: r.f(), Cool: r.f(), Vent: r.f(), Transmitted: r.f(), GlassInward: r.f(), Irr: r.array(), Ho: r.array(), NetworkIR: r.array()}
}
func overrideHour(r *reader, h Hour, mask uint32) Hour {
	ps := []*float64{&h.Air, &h.Ground, &h.SkyIR, &h.Internal, &h.Heat, &h.Cool, &h.Vent, &h.Transmitted, &h.GlassInward}
	for i, p := range ps {
		if mask&(1<<i) != 0 {
			*p = r.f()
		}
	}
	if mask&(1<<9) != 0 {
		h.Irr = r.array()
	}
	if mask&(1<<10) != 0 {
		h.Ho = r.array()
	}
	if mask&(1<<11) != 0 {
		h.NetworkIR = r.array()
	}
	return h
}

type prepared struct {
	p                      Input
	t                      []topology
	n                      int
	w                      workspace
	lastHour               Hour
	lastOut                OutputHour
	lastX                  []float64
	lastLevel              int
	valid                  bool
	solves, hits, rebuilds uint64
}
type binarySession struct {
	models                                                   []prepared
	step                                                     []Input
	stepID                                                   uint64
	warmStart, cacheEnabled                                  bool
	candidate, serial                                        uint64
	evalCalls, getCalls                                      uint64
	decodeSeconds, solveSeconds, formatSeconds, writeSeconds float64
}

func (s *binarySession) handle(op uint16, raw []byte) ([]byte, error) {
	r := &reader{b: raw}
	b := new(bytes.Buffer)
	switch op {
	case 1:
		s.candidate = 0
		count := int(r.u())
		if count < 1 || count > len(raw)/8 {
			return nil, fmt.Errorf("invalid model count")
		}
		models := make([]Input, count)
		for i := range models {
			models[i] = readModel(r)
			if r.err != nil {
				return nil, r.err
			}
		}
		if r.p != len(raw) {
			return nil, fmt.Errorf("trailing model bytes")
		}

		if len(models) == 0 {
			return nil, fmt.Errorf("empty models")
		}
		preparedModels := make([]prepared, len(models))
		for i, p := range models {
			if e := validate(p); e != nil {
				return nil, e
			}
			t, n := makeTopology(p)
			preparedModels[i] = prepared{p: p, t: t, n: n}
		}
		s.models = preparedModels
		s.evalCalls = 0
		s.getCalls = 0
		s.decodeSeconds = 0
		s.solveSeconds = 0
		s.formatSeconds = 0
		s.writeSeconds = 0
		s.step = nil
		s.stepID = 0
		putU(b, uint32(len(models)))
		return b.Bytes(), nil
	case 2:
		id := r.id()
		count := int(r.u())
		if id == 0 || count == 0 || count != len(s.models) {
			return nil, fmt.Errorf("invalid step id or model count")
		}
		step := make([]Input, count)
		for i, m := range s.models {
			p := m.p
			p.Initial = r.array()
			p.Hours = []Hour{readHour(r)}
			if r.err != nil {
				return nil, r.err
			}
			if e := validateHours(p); e != nil {
				return nil, e
			}
			if len(p.Initial) == 0 {
				p.Initial = make([]float64, m.n)
				for j := range p.Initial {
					p.Initial[j] = p.InitialC
				}
			}
			if len(p.Initial) != m.n {
				return nil, fmt.Errorf("initial_state size mismatch")
			}
			for _, v := range p.Initial {
				if !finite(v) || v <= -273.15 {
					return nil, fmt.Errorf("invalid initial state")
				}
			}
			step[i] = p
		}
		if r.p != len(raw) {
			return nil, fmt.Errorf("trailing step bytes")
		}
		s.step = step
		s.candidate = 0
		for i := range s.models {
			s.models[i].valid = false
			s.models[i].w.guess = nil
		}
		s.stepID = id
		putID(b, id)
	case 3, 7:
		s.evalCalls++
		decodeStart := time.Now()
		id := r.id()
		level := int(r.u())
		states := r.u()
		aggregation := r.u()
		mask := r.u()
		if s.step == nil || id != s.stepID || level > 2 || states > 1 || aggregation > 1 || mask >= 1<<12 {
			return nil, fmt.Errorf("invalid evaluate header or stale step")
		}
		batch := make([]Input, len(s.step))
		for i, p := range s.step {
			p.Hours = []Hour{overrideHour(r, p.Hours[0], mask)}
			if r.err != nil {
				return nil, r.err
			}
			if e := validateHours(p); e != nil {
				return nil, e
			}
			batch[i] = p
		}
		if r.p != len(raw) {
			return nil, fmt.Errorf("trailing evaluate bytes")
		}
		s.decodeSeconds += time.Since(decodeStart).Seconds()
		solveStart := time.Now()
		results := make([]Output, len(batch))
		errors := make([]error, len(batch))
		jobs := make(chan int, len(batch))
		var wg sync.WaitGroup
		for i := range batch {
			jobs <- i
		}
		close(jobs)
		for w := 0; w < min(8, runtime.GOMAXPROCS(0), len(batch)); w++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				for i := range jobs {
					m := &s.models[i]
					h, x, e := s.solveBuilding(m, batch[i], level)
					results[i] = Output{Protocol: batch[i].Protocol, Hours: []OutputHour{h}, Final: x}
					errors[i] = e
				}
			}()
		}
		wg.Wait()
		s.solveSeconds += time.Since(solveStart).Seconds()
		for i, e := range errors {
			if e != nil {
				s.candidate = 0
				return nil, fmt.Errorf("building %d: %w", i, e)
			}
		}
		s.serial++
		s.candidate = s.serial
		formatStart := time.Now()
		if op == 7 {
			putID(b, s.candidate)
		}
		writeResults(b, results, level, states, aggregation)
		s.formatSeconds += time.Since(formatStart).Seconds()
	case 5:
		id := r.id()
		if len(raw) != 8 || s.step == nil || id != s.stepID || s.candidate == 0 {
			return nil, fmt.Errorf("no current candidate")
		}
		putID(b, s.candidate)
	case 6:
		id := r.id()
		candidate := r.id()
		level := int(r.u())
		states := r.u()
		aggregation := r.u()
		if len(raw) != 28 || id != s.stepID || s.step == nil || candidate == 0 || candidate != s.candidate || level > 2 || states > 1 || aggregation > 1 {
			return nil, fmt.Errorf("stale candidate or invalid result options")
		}
		s.getCalls++
		formatStart := time.Now()
		results := make([]Output, len(s.models))
		for i := range s.models {
			m := &s.models[i]
			if !m.valid {
				return nil, fmt.Errorf("no saved solution")
			}
			h, x, e := s.promote(m, s.step[i], level)
			if e != nil {
				return nil, e
			}
			results[i] = Output{Protocol: m.p.Protocol, Hours: []OutputHour{h}, Final: x}
		}
		writeResults(b, results, level, states, aggregation)
		s.formatSeconds += time.Since(formatStart).Seconds()
	case 8:
		warm, cache := r.u(), r.u()
		if len(raw) != 8 || warm > 1 || cache > 1 {
			return nil, fmt.Errorf("invalid solver options")
		}
		s.warmStart = warm == 1
		s.cacheEnabled = cache == 1
		s.candidate = 0
		for i := range s.models {
			s.models[i].valid = false
			s.models[i].w.guess = nil
		}
		putU(b, warm)
		putU(b, cache)
	case 9:
		if len(raw) != 0 {
			return nil, fmt.Errorf("profile takes no payload")
		}
		solves, hits, rebuilds, iters, warm, fallback := uint64(0), uint64(0), uint64(0), uint64(0), uint64(0), uint64(0)
		for _, m := range s.models {
			solves += m.solves
			hits += m.hits
			rebuilds += m.rebuilds
			iters += m.w.iterations
			warm += m.w.warmStarts
			fallback += m.w.fallbacks
		}
		for _, v := range []uint64{s.evalCalls, s.getCalls, solves, hits, rebuilds, iters, warm, fallback} {
			putID(b, v)
		}
		for _, v := range []float64{s.decodeSeconds, s.solveSeconds, s.formatSeconds, s.writeSeconds} {
			putF(b, v)
		}

	case 10:
		if s.step != nil {
			return nil, fmt.Errorf("spectral registration requires an inactive session")
		}
		count := int(r.u())
		if count != len(s.models) {
			return nil, fmt.Errorf("spectral model count differs")
		}
		all := make([][]Surface, count)
		for i, m := range s.models {
			n := int(r.u())
			if n != len(m.p.Surfaces) {
				return nil, fmt.Errorf("spectral surface count differs")
			}
			surfaces := append([]Surface(nil), m.p.Surfaces...)
			for j := range surfaces {
				nb := int(r.u())
				if nb < 0 || nb > 128 {
					return nil, fmt.Errorf("invalid spectral band count")
				}
				surfaces[j].LWBands = make([]LWBand, nb)
				for k := range surfaces[j].LWBands {
					lo, hi, e := r.f(), r.f(), r.f()
					var upper *float64
					if hi != -1 {
						v := hi
						upper = &v
					}
					surfaces[j].LWBands[k] = LWBand{Lower: lo, Upper: upper, Eps: e}
				}
				if r.err != nil {
					return nil, r.err
				}
				if e := prepareBands(&surfaces[j]); e != nil {
					return nil, e
				}
			}
			all[i] = surfaces
		}
		if r.p != len(raw) {
			return nil, fmt.Errorf("trailing spectral registration bytes")
		}
		for i := range s.models {
			s.models[i].p.Surfaces = all[i]
			s.models[i].valid = false
			s.models[i].w.guess = nil
		}
		putU(b, uint32(count))
	case 4:
		id := r.id()
		if len(raw) != 8 || id != s.stepID {
			return nil, fmt.Errorf("stale step")
		}
		s.step = nil
		s.candidate = 0
		putID(b, id)
	default:
		return nil, fmt.Errorf("unknown opcode")
	}
	return b.Bytes(), r.err
}
func frameWrite(out io.Writer, op uint16, id uint64, raw []byte) error {
	var h [24]byte
	copy(h[:4], "CES3")
	le.PutUint16(h[4:6], 1)
	le.PutUint16(h[6:8], op)
	le.PutUint64(h[8:16], id)
	le.PutUint64(h[16:24], uint64(len(raw)))
	if _, e := out.Write(h[:]); e != nil {
		return e
	}
	_, e := out.Write(raw)
	return e
}
func binaryMain() {
	s := &binarySession{warmStart: true, cacheEnabled: true}
	for {
		var h [24]byte
		if _, e := io.ReadFull(os.Stdin, h[:]); e != nil {
			if e != io.EOF {
				fmt.Fprintln(os.Stderr, e)
			}
			return
		}
		op := le.Uint16(h[6:8])
		id := le.Uint64(h[8:16])
		n := le.Uint64(h[16:24])
		if string(h[:4]) != "CES3" || le.Uint16(h[4:6]) != 1 || n > maxFrame {
			fmt.Fprintln(os.Stderr, "invalid frame header")
			return
		}
		raw := make([]byte, int(n))
		if _, e := io.ReadFull(os.Stdin, raw); e != nil {
			return
		}
		data, e := s.handle(op, raw)
		if e == nil && len(data) > maxFrame {
			e = fmt.Errorf("response exceeds 256 MiB; split the batch")
		}
		if e != nil {
			op = 65535
			data = []byte(e.Error())
		}
		writeStart := time.Now()
		if frameWrite(os.Stdout, op, id, data) != nil {
			return
		}
		s.writeSeconds += time.Since(writeStart).Seconds()
	}
}

func writeFull(b *bytes.Buffer, h OutputHour) {
	putF(b, h.Radiant)
	putF(b, h.NetworkLW)
	putF(b, h.Zone)
	putF(b, h.HVAC)
	putF(b, h.Electricity)
	putF(b, h.Convection)
	putF(b, h.Ventilation)
	putF(b, h.OutdoorHVAC)
	putF(b, h.GroundLW)
	putF(b, h.GroundContact)
	putF(b, h.SkyLW)
	putF(b, h.Storage)
	putF(b, h.Residual)
	putU(b, uint32(len(h.Surfaces)))
	for _, s := range h.Surfaces {
		putF(b, s.StructureIntoInterior)
		putF(b, s.ExteriorConvection)
		putF(b, s.InteriorConvection)
		putF(b, s.InteriorLW)
		putF(b, s.SolarDeposited)
		putF(b, s.ExteriorContact)
		putF(b, s.ExternalLW)
		putF(b, s.BodyResidual)
		putF(b, s.Te)
		putF(b, s.Ti)
		putF(b, s.Storage)
		putF(b, s.Delta)
		putF(b, s.Incident)
		putF(b, s.Reflected)
		putF(b, s.Absorbed)
		putF(b, s.Transmitted)
	}
}

func (r *reader) text() string {
	n := int(r.u())
	if n > len(r.b)-r.p {
		r.err = fmt.Errorf("invalid string length")
		return ""
	}
	return string(r.take(n))
}
func readModel(r *reader) Input {
	p := Input{Protocol: r.text(), Dt: r.f(), InitialC: r.f(), COPc: r.f(), COPh: r.f(), Radiant: r.f(), SolarFloor: r.f()}
	count := int(r.u())
	if count > len(r.b)/8 {
		r.err = fmt.Errorf("invalid surface count")
		return p
	}
	p.Surfaces = make([]Surface, count)
	for i := range p.Surfaces {
		s := Surface{Name: r.text(), Kind: r.text(), Area: r.f(), R: r.f(), Mass: r.f(), Rho: r.f(), Tau: r.f(), Eps: r.f(), Hi: r.f(), Ho: r.f(), Sky: r.f(), Ground: r.f(), GroundH: r.f(), AbsInside: r.f()}
		n := int(r.u())
		if n > (len(r.b)-r.p)/16 {
			r.err = fmt.Errorf("invalid layer count")
			return p
		}
		s.Layers = make([]Layer, n)
		for j := range s.Layers {
			s.Layers[j] = Layer{R: r.f(), C: r.f()}
		}
		p.Surfaces[i] = s
	}
	n := int(r.u())
	if n > (len(r.b)-r.p)/84 {
		r.err = fmt.Errorf("invalid hour count")
		return p
	}
	p.Hours = make([]Hour, n)
	for j := range p.Hours {
		p.Hours[j] = readHour(r)
	}
	p.Initial = r.array()
	return p
}

func writeResults(b *bytes.Buffer, results []Output, level int, states, aggregation uint32) {
	air, ground, res := 0., 0., 0.
	for _, o := range results {

		h := o.Hours[0]
		air += h.Convection + h.Ventilation + h.OutdoorHVAC
		ground += h.GroundLW + h.GroundContact
		res = math.Max(res, math.Abs(h.Residual))
	}
	putU(b, uint32(len(results)))
	putF(b, air)
	putF(b, ground)
	putF(b, res)
	if aggregation == 1 {
		for _, o := range results {
			h := o.Hours[0]
			putF(b, h.Convection+h.Ventilation+h.OutdoorHVAC)
			putF(b, h.GroundLW+h.GroundContact)
		}
	}
	for _, o := range results {
		h := o.Hours[0]
		if level == 1 {
			putU(b, uint32(len(h.Surfaces)))
			for _, v := range h.Surfaces {
				putF(b, v.Te)
			}
		}
		if level == 2 {
			writeFull(b, h)
		}
		if states == 1 {
			putArray(b, o.Final)
		}
	}
}

func sameArray(a, b []float64) bool {
	if len(a) != len(b) {
		return false
	}
	for i, v := range a {
		if math.Float64bits(v) != math.Float64bits(b[i]) {
			return false
		}
	}
	return true
}
func sameHour(a, b Hour) bool {
	av := [...]float64{a.Air, a.Ground, a.SkyIR, a.Internal, a.Heat, a.Cool, a.Vent, a.Transmitted, a.GlassInward}
	bv := [...]float64{b.Air, b.Ground, b.SkyIR, b.Internal, b.Heat, b.Cool, b.Vent, b.Transmitted, b.GlassInward}
	for i, v := range av {
		if math.Float64bits(v) != math.Float64bits(bv[i]) {
			return false
		}
	}
	return sameArray(a.Irr, b.Irr) && sameArray(a.Ho, b.Ho) && sameArray(a.NetworkIR, b.NetworkIR)
}

func (s *binarySession) promote(m *prepared, p Input, level int) (OutputHour, []float64, error) {
	if level > m.lastLevel {
		h, x, e := outputAt(p, m.lastHour, p.Initial, m.t, m.n, level, m.lastX, m.lastOut.HVAC)
		if e != nil {
			return h, x, e
		}
		m.lastOut = h
		m.lastLevel = level
		m.rebuilds++
	}
	return m.lastOut, m.lastX, nil
}
func (s *binarySession) solveBuilding(m *prepared, p Input, level int) (OutputHour, []float64, error) {
	if s.cacheEnabled && m.valid && sameHour(m.lastHour, p.Hours[0]) {
		m.hits++
		return s.promote(m, p, level)
	}
	m.w.useGuess = s.warmStart
	m.solves++
	h, x, e := hourPrepared(p, p.Hours[0], p.Initial, m.t, m.n, level, &m.w)
	if e != nil && s.warmStart && len(m.w.guess) > 0 {
		m.w.guess = nil
		m.w.fallbacks++
		m.solves++
		h, x, e = hourPrepared(p, p.Hours[0], p.Initial, m.t, m.n, level, &m.w)
	}
	if e == nil {
		m.lastHour = p.Hours[0]
		m.lastOut = h
		m.lastX = x
		m.lastLevel = level
		m.valid = true
	} else {
		m.valid = false
	}
	return h, x, e
}
