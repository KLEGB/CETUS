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
	p Input
	t []topology
	n int
	w workspace
}
type binarySession struct {
	models []prepared
	step   []Input
	stepID uint64
}

func (s *binarySession) handle(op uint16, raw []byte) ([]byte, error) {
	r := &reader{b: raw}
	b := new(bytes.Buffer)
	switch op {
	case 1:
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
		s.stepID = id
		putID(b, id)
	case 3:
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
					h, x, e := hourPrepared(batch[i], batch[i].Hours[0], batch[i].Initial, m.t, m.n, level, &m.w)
					results[i] = Output{Protocol: batch[i].Protocol, Hours: []OutputHour{h}, Final: x}
					errors[i] = e
				}
			}()
		}
		wg.Wait()
		air, ground, res := 0., 0., 0.
		for i, o := range results {
			if errors[i] != nil {
				return nil, fmt.Errorf("building %d: %w", i, errors[i])
			}
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
	case 4:
		id := r.id()
		if len(raw) != 8 || id != s.stepID {
			return nil, fmt.Errorf("stale step")
		}
		s.step = nil
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
	s := new(binarySession)
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
		if frameWrite(os.Stdout, op, id, data) != nil {
			return
		}
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
