// Surface energy: dynamic constructions, algebraic surface films and zone air.
// Temperatures C, powers W, capacities J/K, duration seconds. No global state.
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"math"
	"os"
	"runtime"
	"sync"
)

const sigma = 5.670374419e-8

type Layer struct {
	R float64 `json:"resistance_m2k_w"`
	C float64 `json:"capacity_j_m2k"`
}
type Surface struct {
	Name      string  `json:"name"`
	Kind      string  `json:"kind"`
	Area      float64 `json:"area_m2"`
	R         float64 `json:"resistance_m2k_w"` // construction only; reference films removed by adapter
	Mass      float64 `json:"thermal_mass_j_m2k"`
	Rho       float64 `json:"reflectance"`
	Tau       float64 `json:"transmittance"`
	Eps       float64 `json:"emissivity"`
	Hi        float64 `json:"hi_w_m2k"`
	Ho        float64 `json:"ho_w_m2k"`
	Sky       float64 `json:"sky_view"`
	Ground    float64 `json:"ground_view"`
	GroundH   float64 `json:"ground_contact_w_m2k"`
	AbsInside float64 `json:"absorbed_inside_fraction"`
	Layers    []Layer `json:"layers,omitempty"`
}
type Hour struct {
	Air      float64   `json:"air_c"`
	Ground   float64   `json:"ground_c"`
	SkyIR    float64   `json:"sky_ir_w_m2"`
	Irr      []float64 `json:"irradiance_w_m2"`
	Internal float64   `json:"internal_w"`
	Heat     float64   `json:"heat_setpoint_c"`
	Cool     float64   `json:"cool_setpoint_c"`
	Vent     float64   `json:"ventilation_w_k"`
	Ho       []float64 `json:"ho_w_m2k,omitempty"`
	// Explicit compatibility gains only for validation inputs; never combined with optical gains.
	Transmitted float64 `json:"additional_transmitted_w,omitempty"`
	GlassInward float64 `json:"additional_glass_inward_w,omitempty"`
}
type Input struct {
	Protocol   string    `json:"protocol"`
	Dt         float64   `json:"dt_s"`
	Surfaces   []Surface `json:"surfaces"`
	Hours      []Hour    `json:"hours"`
	Initial    []float64 `json:"initial_state,omitempty"`
	InitialC   float64   `json:"initial_c"`
	COPc       float64   `json:"cooling_cop"`
	COPh       float64   `json:"heating_cop"`
	Radiant    float64   `json:"internal_radiant_fraction"`
	SolarFloor float64   `json:"solar_floor_fraction"`
}
type SurfaceOut struct {
	Name        string  `json:"name"`
	Te          float64 `json:"exterior_c"`
	Ti          float64 `json:"interior_c"`
	Storage     float64 `json:"storage_w"`
	Delta       float64 `json:"storage_delta_kwh"`
	Incident    float64 `json:"incident_w"`
	Reflected   float64 `json:"reflected_w"`
	Absorbed    float64 `json:"absorbed_w"`
	Transmitted float64 `json:"transmitted_w"`
}
type OutputHour struct {
	Zone          float64      `json:"zone_c"`
	HVAC          float64      `json:"hvac_w"` // positive heating, negative cooling
	Electricity   float64      `json:"electricity_w"`
	Convection    float64      `json:"convection_to_air_w"`
	Ventilation   float64      `json:"ventilation_to_air_w"`
	OutdoorHVAC   float64      `json:"hvac_to_air_w"`
	GroundLW      float64      `json:"longwave_to_ground_w"`
	GroundContact float64      `json:"contact_to_ground_w"`
	SkyLW         float64      `json:"longwave_to_sky_w"`
	Storage       float64      `json:"storage_w"`
	Residual      float64      `json:"balance_residual_w"`
	Surfaces      []SurfaceOut `json:"surfaces"`
}
type Output struct {
	Protocol string       `json:"protocol"`
	Hours    []OutputHour `json:"hours"`
	Final    []float64    `json:"final_state"`
}
type topology struct {
	ext, in int
	mid     []int
	caps    []float64
	links   []float64
}

func finite(x float64) bool { return !math.IsNaN(x) && !math.IsInf(x, 0) }
func validate(p Input) error {
	if p.Protocol != "cetus-surface-7" || p.Dt <= 0 || p.COPc <= 0 || p.COPh <= 0 || len(p.Surfaces) == 0 || len(p.Hours) == 0 {
		return fmt.Errorf("invalid protocol, dt, COP or empty input")
	}
	if p.Radiant < 0 || p.Radiant > 1 || p.SolarFloor < 0 || p.SolarFloor > 1 {
		return fmt.Errorf("invalid radiant or solar floor fraction")
	}
	names := map[string]bool{}
	for _, s := range p.Surfaces {
		if names[s.Name] || s.Name == "" {
			return fmt.Errorf("duplicate/empty surface name")
		}
		names[s.Name] = true
		if s.Kind != "wall" && s.Kind != "roof" && s.Kind != "floor" && s.Kind != "window" && s.Kind != "skylight" {
			return fmt.Errorf("unknown kind %s", s.Kind)
		}
		v := []float64{s.Area, s.R, s.Mass, s.Rho, s.Tau, s.Eps, s.Hi, s.Ho, s.Sky, s.Ground, s.GroundH, s.AbsInside}
		for _, x := range v {
			if !finite(x) || x < 0 {
				return fmt.Errorf("invalid surface %s", s.Name)
			}
		}
		if s.Area <= 0 || s.R <= 0 || s.Hi <= 0 || s.Eps > 1 || s.Rho+s.Tau > 1+1e-12 || s.Sky+s.Ground > 1+1e-12 || s.AbsInside > 1 {
			return fmt.Errorf("invalid optical/thermal parameters %s", s.Name)
		}
		transparent := s.Kind == "window" || s.Kind == "skylight"
		if transparent && (s.Mass != 0 || len(s.Layers) != 0) {
			return fmt.Errorf("transparent surface must have zero capacity")
		}
		if !transparent && s.Tau != 0 {
			return fmt.Errorf("opaque transmission must be zero")
		}
		if s.Kind == "floor" && (s.GroundH <= 0 || s.Ho != 0 || s.Sky != 0 || s.Ground != 0) {
			return fmt.Errorf("floor requires contact boundary only")
		}
		if s.Kind != "floor" && math.Abs(s.Sky+s.Ground-1) > 1e-10 {
			return fmt.Errorf("external view factors must sum to one")
		}
		for _, l := range s.Layers {
			if !finite(l.R) || !finite(l.C) || l.R <= 0 || l.C < 0 {
				return fmt.Errorf("invalid layer")
			}
		}
	}
	for _, h := range p.Hours {
		if len(h.Irr) != len(p.Surfaces) || (len(h.Ho) != 0 && len(h.Ho) != len(p.Surfaces)) {
			return fmt.Errorf("hourly surface array size mismatch")
		}
		for _, x := range []float64{h.Air, h.Ground, h.SkyIR, h.Internal, h.Heat, h.Cool, h.Vent, h.Transmitted, h.GlassInward} {
			if !finite(x) {
				return fmt.Errorf("nonfinite hour")
			}
		}
		if h.Air <= -273.15 || h.Ground <= -273.15 || h.SkyIR <= 0 || h.Internal < 0 || h.Heat > h.Cool || h.Vent < 0 || h.Transmitted < 0 || h.GlassInward < 0 {
			return fmt.Errorf("invalid hour")
		}
		for _, a := range [][]float64{h.Irr, h.Ho} {
			for _, x := range a {
				if !finite(x) || x < 0 {
					return fmt.Errorf("invalid irradiation or film")
				}
			}
		}
	}
	return nil
}
func makeTopology(p Input) ([]topology, int) {
	t := make([]topology, len(p.Surfaces))
	n := 0
	for k, s := range p.Surfaces {
		t[k].ext = n
		n++
		ls := s.Layers
		if len(ls) == 0 {
			ls = []Layer{{s.R, s.Mass}}
		}
		if s.Kind == "window" || s.Kind == "skylight" {
			t[k].links = []float64{s.Area / s.R}
		} else {
			for i, l := range ls {
				t[k].mid = append(t[k].mid, n)
				t[k].caps = append(t[k].caps, l.C*s.Area)
				n++
				if i == 0 {
					t[k].links = append(t[k].links, 2*s.Area/l.R)
				} else {
					t[k].links = append(t[k].links, 2*s.Area/(ls[i-1].R+l.R))
				}
			}
			t[k].links = append(t[k].links, 2*s.Area/ls[len(ls)-1].R)
		}
		t[k].in = n
		n++
	}
	return t, n + 2 // zone air and massless indoor radiant enclosure
}
func matrix(n int) ([][]float64, []float64) {
	a := make([][]float64, n)
	for i := range a {
		a[i] = make([]float64, n)
	}
	return a, make([]float64, n)
}
func link(a [][]float64, i, j int, g float64)                     { a[i][i] += g; a[j][j] += g; a[i][j] -= g; a[j][i] -= g }
func boundary(a [][]float64, b []float64, i int, g, temp float64) { a[i][i] += g; b[i] += g * temp }
func solve(a [][]float64, b []float64) ([]float64, error) {
	n := len(b)
	for i := 0; i < n; i++ {
		pivot := i
		for j := i + 1; j < n; j++ {
			if math.Abs(a[j][i]) > math.Abs(a[pivot][i]) {
				pivot = j
			}
		}
		if math.Abs(a[pivot][i]) < 1e-14 {
			return nil, fmt.Errorf("singular thermal network")
		}
		a[i], a[pivot] = a[pivot], a[i]
		b[i], b[pivot] = b[pivot], b[i]
		for j := i + 1; j < n; j++ {
			f := a[j][i] / a[i][i]
			for k := i + 1; k < n; k++ {
				a[j][k] -= f * a[i][k]
			}
			b[j] -= f * b[i]
		}
	}
	x := make([]float64, n)
	for i := n - 1; i >= 0; i-- {
		v := b[i]
		for j := i + 1; j < n; j++ {
			v -= a[i][j] * x[j]
		}
		x[i] = v / a[i][i]
	}
	return x, nil
}
func hr(e, a, b float64) float64 { a += 273.15; b += 273.15; return e * sigma * (a + b) * (a*a + b*b) }
func fourth(c float64) float64   { return math.Pow(c+273.15, 4) }
func hour(p Input, h Hour, old []float64, t []topology, n int) (OutputHour, []float64, error) {
	z, rad := n-2, n-1
	x := append([]float64(nil), old...)
	sky := math.Pow(h.SkyIR/sigma, .25) - 273.15
	solar := h.Transmitted
	var sumArea, floorArea float64
	for k, s := range p.Surfaces {
		solar += s.Area * h.Irr[k] * s.Tau
		sumArea += s.Area
		if s.Kind == "floor" {
			floorArea += s.Area
		}
	}
	weights := make([]float64, len(t))
	for k, s := range p.Surfaces {
		weights[k] = s.Area / sumArea
		if floorArea > 0 {
			weights[k] = (1 - p.SolarFloor) * s.Area / sumArea
			if s.Kind == "floor" {
				weights[k] += p.SolarFloor * s.Area / floorArea
			}
		}
	}
	var qhvac float64
	// The free-running trial and thermostat trial share the immutable old state.
	fixed := false
	set := 0.0
	for mode := 0; mode < 2; mode++ {
		converged := false
		for iter := 0; iter < 100; iter++ {
			a, b := matrix(n)
			for k, s := range p.Surfaces {
				v := t[k]
				nodes := append([]int{v.ext}, v.mid...)
				nodes = append(nodes, v.in)
				for j, g := range v.links {
					link(a, nodes[j], nodes[j+1], g)
				}
				for j, id := range v.mid {
					boundary(a, b, id, v.caps[j]/p.Dt, old[id])
				}
				ho := s.Ho
				if len(h.Ho) > 0 {
					ho = h.Ho[k]
				}
				boundary(a, b, v.ext, ho*s.Area, h.Air)
				boundary(a, b, v.ext, s.GroundH*s.Area, h.Ground)
				boundary(a, b, v.ext, hr(s.Eps, x[v.ext], sky)*s.Area*s.Sky, sky)
				boundary(a, b, v.ext, hr(s.Eps, x[v.ext], h.Ground)*s.Area*s.Ground, h.Ground)
				absorbed := (1 - s.Rho - s.Tau) * h.Irr[k] * s.Area
				b[v.ext] += absorbed * (1 - s.AbsInside)
				b[v.in] += absorbed*s.AbsInside + solar*weights[k]
				if s.Kind == "window" || s.Kind == "skylight" {
					b[v.in] += h.GlassInward * s.Area / transparentArea(p)
				}
				link(a, v.in, z, s.Hi*s.Area)
				link(a, v.in, rad, hr(s.Eps, x[v.in], x[rad])*s.Area)
			}
			b[rad] += h.Internal * p.Radiant
			b[z] += h.Internal * (1 - p.Radiant)
			boundary(a, b, z, h.Vent, h.Air)
			airRow := append([]float64(nil), a[z]...)
			airB := b[z]
			if fixed {
				for j := range a[z] {
					a[z][j] = 0
				}
				a[z][z] = 1
				b[z] = set
			}
			next, err := solve(a, b)
			if err != nil {
				return OutputHour{}, nil, err
			}
			delta := 0.0
			for j := range x {
				if !finite(next[j]) || next[j] <= -273.15 || next[j] > 1000 {
					return OutputHour{}, nil, fmt.Errorf("nonphysical node temperature")
				}
				delta = math.Max(delta, math.Abs(next[j]-x[j]))
			}
			x = next
			qhvac = 0
			for j, v := range airRow {
				qhvac += v * x[j]
			}
			qhvac -= airB
			if delta < 1e-8 {
				converged = true
				break
			}
		}
		if !converged {
			return OutputHour{}, nil, fmt.Errorf("surface network failed to converge")
		}
		if !fixed && x[z] < h.Heat {
			fixed = true
			set = h.Heat
		} else if !fixed && x[z] > h.Cool {
			fixed = true
			set = h.Cool
		} else {
			break
		}
	}
	out := OutputHour{Zone: x[z], HVAC: qhvac}
	if qhvac >= 0 {
		out.Electricity = qhvac / p.COPh
	} else {
		out.Electricity = -qhvac / p.COPc
	}
	out.OutdoorHVAC = -qhvac + out.Electricity
	out.Ventilation = h.Vent * (x[z] - h.Air)
	var sw float64
	for k, s := range p.Surfaces {
		v := t[k]
		o := SurfaceOut{Name: s.Name, Te: x[v.ext], Ti: x[v.in], Incident: s.Area * h.Irr[k]}
		o.Reflected = o.Incident * s.Rho
		o.Transmitted = o.Incident * s.Tau
		o.Absorbed = o.Incident - o.Reflected - o.Transmitted
		for j, id := range v.mid {
			o.Storage += v.caps[j] * (x[id] - old[id]) / p.Dt
		}
		o.Delta = o.Storage * p.Dt / 3.6e6
		out.Storage += o.Storage
		out.Surfaces = append(out.Surfaces, o)
		sw += o.Transmitted + o.Absorbed
		ho := s.Ho
		if len(h.Ho) > 0 {
			ho = h.Ho[k]
		}
		out.Convection += ho * s.Area * (o.Te - h.Air)
		out.GroundContact += s.GroundH * s.Area * (o.Te - h.Ground)
		out.GroundLW += s.Area * s.Eps * s.Ground * sigma * (fourth(o.Te) - fourth(h.Ground))
		out.SkyLW += s.Area * s.Eps * s.Sky * (sigma*fourth(o.Te) - h.SkyIR)
	}
	out.Residual = sw + h.Transmitted + h.GlassInward + h.Internal + qhvac - out.Convection - out.Ventilation - out.GroundContact - out.GroundLW - out.SkyLW - out.Storage
	if math.Abs(out.Residual) > 1e-4+1e-9*(sw+h.Internal+math.Abs(qhvac)) {
		return OutputHour{}, nil, fmt.Errorf("building balance residual %g W", out.Residual)
	}
	return out, x, nil
}
func transparentArea(p Input) float64 {
	a := 0.0
	for _, s := range p.Surfaces {
		if s.Kind == "window" || s.Kind == "skylight" {
			a += s.Area
		}
	}
	if a == 0 {
		return 1
	}
	return a
}
func run(p Input) (Output, error) {
	if err := validate(p); err != nil {
		return Output{}, err
	}
	t, n := makeTopology(p)
	state := append([]float64(nil), p.Initial...)
	if len(state) == 0 {
		state = make([]float64, n)
		for i := range state {
			state[i] = p.InitialC
		}
	}
	if len(state) != n {
		return Output{}, fmt.Errorf("initial_state size: expected %d", n)
	}
	for _, v := range state {
		if !finite(v) || v <= -273.15 {
			return Output{}, fmt.Errorf("invalid initial_state")
		}
	}
	out := Output{Protocol: p.Protocol, Hours: make([]OutputHour, 0, len(p.Hours))}
	for i, h := range p.Hours {
		o, s, e := hour(p, h, state, t, n)
		if e != nil {
			return Output{}, fmt.Errorf("hour %d: %w", i, e)
		}
		out.Hours = append(out.Hours, o)
		state = s
	}
	out.Final = state
	return out, nil
}

// Persistent JSON stream, including one request per building or a batch per hour.
type Request struct {
	Models    []Input `json:"models,omitempty"`
	UseModels bool    `json:"use_models,omitempty"`
	Trial     bool    `json:"trial,omitempty"`
	Batch     []Input `json:"batch,omitempty"`
	Input
}

// Buildings are independent within a trial. Preserve input order for deterministic sums.
func runBatch(req Request, models []Input) ([]Output, error) {
	if req.UseModels && len(models) != len(req.Batch) {
		return nil, fmt.Errorf("model batch size mismatch")
	}
	results := make([]Output, len(req.Batch))
	errors := make([]error, len(req.Batch))
	workers := min(8, runtime.GOMAXPROCS(0), len(req.Batch))
	jobs := make(chan int, len(req.Batch))
	var wg sync.WaitGroup
	for i := range req.Batch {
		jobs <- i
	}
	close(jobs)
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := range jobs {
				p := req.Batch[i]
				if req.UseModels {
					current := models[i]
					current.Hours = p.Hours
					current.Initial = p.Initial
					p = current
				}
				results[i], errors[i] = run(p)
			}
		}()
	}
	wg.Wait()
	for i, e := range errors {
		if e != nil {
			return nil, fmt.Errorf("building %d: %w", i, e)
		}
	}
	return results, nil
}

func main() {
	in, out := io.Reader(os.Stdin), io.Writer(os.Stdout)
	if len(os.Args) == 3 {
		f, e := os.Open(os.Args[1])
		if e != nil {
			panic(e)
		}
		defer f.Close()
		in = f
		g, e := os.Create(os.Args[2])
		if e != nil {
			panic(e)
		}
		defer g.Close()
		out = g
	}
	dec, enc := json.NewDecoder(in), json.NewEncoder(out)
	dec.DisallowUnknownFields()
	var models []Input
	for {
		var req Request
		e := dec.Decode(&req)
		if e == io.EOF {
			return
		}
		if e != nil {
			fmt.Fprintln(os.Stderr, e)
			os.Exit(1)
		}
		if req.Models != nil {
			models = req.Models
			if e := enc.Encode(map[string]int{"configured": len(models)}); e != nil {
				panic(e)
			}
			continue
		}
		var result any
		if req.Batch != nil {
			r, e := runBatch(req, models)
			if e != nil {
				fmt.Fprintln(os.Stderr, e)
				os.Exit(1)
			}
			if req.Trial {
				air, ground := 0.0, 0.0
				for _, o := range r {
					h := o.Hours[0]
					air += h.Convection + h.Ventilation + h.OutdoorHVAC
					ground += h.GroundLW + h.GroundContact
				}
				result = map[string]float64{"air_w": air, "ground_w": ground}
			} else {
				result = map[string]any{"batch": r}
			}
		} else {
			o, e := run(req.Input)
			if e != nil {
				fmt.Fprintln(os.Stderr, e)
				os.Exit(1)
			}
			result = o
		}
		if e := enc.Encode(result); e != nil {
			fmt.Fprintln(os.Stderr, e)
			os.Exit(1)
		}
	}
}
