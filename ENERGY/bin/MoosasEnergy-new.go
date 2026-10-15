// Steady heat transfer with conservative, offline daily solar redistribution.
// No RC/CTF states; the empirical preprocessing uses full-day information.
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"math"
	"os"
	"reflect"
	"strings"
)

type Parameters struct {
	InteriorConvection float64 `json:"interior_convection"`
	ExteriorFilm       float64 `json:"exterior_film"`
	SolarFloorFraction float64 `json:"solar_floor_fraction"`
}
type Surface struct {
	Name        string  `json:"name"`
	Area        float64 `json:"area"`
	U           float64 `json:"u"`
	Resistance  float64 `json:"resistance"`
	Hi          float64 `json:"hi"`
	Ho          float64 `json:"ho"`
	SkyView     float64 `json:"sky_view"`
	Absorptance float64 `json:"absorptance"`
}
type Hour struct {
	HourEnding       int       `json:"hour_ending"`
	AirC             float64   `json:"air_c"`
	BoundaryC        []float64 `json:"boundary_c"`
	Irradiance       []float64 `json:"irradiance_w_m2"`
	SkyIR            float64   `json:"sky_ir_w_m2"`
	SolarTransmitted float64   `json:"solar_transmitted_w"`
	SolarAbsorbed    float64   `json:"solar_absorbed_inward_w"`
	Internal         float64   `json:"internal_w"`
	HeatSetpoint     float64   `json:"heat_setpoint_c"`
	CoolSetpoint     float64   `json:"cool_setpoint_c"`
}
type Input struct {
	Protocol        string         `json:"protocol"`
	Stage           string         `json:"stage"`
	Volume          float64        `json:"volume_m3"`
	ACH             float64        `json:"infiltration_ach"`
	Density         float64        `json:"density_kg_m3"`
	CP              float64        `json:"cp_j_kg_k"`
	CoolingCOP      float64        `json:"cooling_cop"`
	HeatingCOP      float64        `json:"heating_cop"`
	RadiantFraction float64        `json:"internal_radiant_fraction"`
	Parameters      Parameters     `json:"parameters"`
	Surfaces        []Surface      `json:"surfaces"`
	Hours           []Hour         `json:"hours"`
	Redistribution  Redistribution `json:"redistribution"`
}
type Redistribution struct {
	WindowFraction     float64 `json:"window_fraction"`
	OpaqueFraction     float64 `json:"opaque_fraction"`
	DelayHours         float64 `json:"delay_hours"`
	WidthHours         float64 `json:"width_hours"`
	BackgroundFraction float64 `json:"background_fraction"`
}
type DayAudit struct {
	Day          int       `json:"day_of_year"`
	WindowBefore float64   `json:"window_before_wh"`
	WindowAfter  float64   `json:"window_after_wh"`
	OpaqueBefore []float64 `json:"opaque_absorbed_before_wh"`
	OpaqueAfter  []float64 `json:"opaque_absorbed_after_wh"`
}
type Balance struct {
	AirC              float64   `json:"zone_air_c"`
	SurfaceC          []float64 `json:"surface_c"`
	SurfaceToAir      []float64 `json:"surface_to_air_w"`
	BoundaryToSurface []float64 `json:"boundary_to_surface_w"`
	ContactTransfer   []float64 `json:"contact_transfer_w"`
	Infiltration      float64   `json:"infiltration_w"`
	DirectInternal    float64   `json:"direct_internal_w"`
	DirectSolar       float64   `json:"direct_solar_w"`
	AbsorbedSolar     float64   `json:"retained_transmitted_solar_w"`
	RadiantInternal   float64   `json:"radiant_internal_w"`
	Net               float64   `json:"net_air_w"`
	Residual          float64   `json:"surface_balance_residual_w"`
}
type Result struct {
	HourEnding  int     `json:"hour_ending"`
	Heating     float64 `json:"heating_kwh"`
	Cooling     float64 `json:"cooling_kwh"`
	Signed      float64 `json:"signed_kwh"`
	Electricity float64 `json:"electricity_kwh"`
	Balance     Balance `json:"balance"`
}
type Output struct {
	Protocol            string             `json:"protocol"`
	ResolvedInput       Input              `json:"resolved_input"`
	Hours               []Result           `json:"hours"`
	Totals              map[string]float64 `json:"totals"`
	EffectiveHours      []Hour             `json:"effective_hours"`
	DailyRedistribution []DayAudit         `json:"daily_redistribution"`
}

// Require every declared field, including zero values; unknown fields are rejected
// by Decoder. Positional old-protocol input and omitted adiabatic U cannot pass.
func requireFields(raw json.RawMessage, t reflect.Type, path string) error {
	if string(raw) == "null" {
		return fmt.Errorf("%s: null is not allowed", path)
	}
	if t.Kind() == reflect.Struct {
		var m map[string]json.RawMessage
		if err := json.Unmarshal(raw, &m); err != nil {
			return err
		}
		for i := 0; i < t.NumField(); i++ {
			f := t.Field(i)
			n := strings.Split(f.Tag.Get("json"), ",")[0]
			v, ok := m[n]
			if !ok {
				return fmt.Errorf("missing %s.%s", path, n)
			}
			if err := requireFields(v, f.Type, path+"."+n); err != nil {
				return err
			}
		}
	} else if t.Kind() == reflect.Slice {
		var a []json.RawMessage
		if err := json.Unmarshal(raw, &a); err != nil {
			return err
		}
		for i, v := range a {
			if err := requireFields(v, t.Elem(), fmt.Sprintf("%s[%d]", path, i)); err != nil {
				return err
			}
		}
	}
	return nil
}
func validate(in Input) error {
	if in.Protocol != "moosas-steady-4" {
		return fmt.Errorf("unsupported protocol")
	}
	if in.Stage != "R2" && in.Stage != "R3" && in.Stage != "R4" && in.Stage != "R5" {
		return fmt.Errorf("invalid stage")
	}
	if in.Volume <= 0 || in.ACH < 0 || in.Density <= 0 || in.CP <= 0 || in.CoolingCOP <= 0 || in.HeatingCOP <= 0 || in.RadiantFraction < 0 || in.RadiantFraction > 1 {
		return fmt.Errorf("invalid physical inputs")
	}
	p := in.Parameters
	r := in.Redistribution
	if in.Stage == "R5" {
		if r.WindowFraction < 0 || r.WindowFraction > 1 || r.OpaqueFraction < 0 || r.OpaqueFraction > 1 || r.DelayHours < 0 || r.DelayHours > 4 || r.WidthHours < 1 || r.WidthHours > 7 || r.BackgroundFraction < 0 || r.BackgroundFraction > .5 {
			return fmt.Errorf("invalid redistribution parameters")
		}
		if len(in.Hours)%24 != 0 {
			return fmt.Errorf("R5 requires complete days of 24 hours")
		}
		for j, h := range in.Hours {
			if (h.HourEnding-1)%24 != j%24 {
				return fmt.Errorf("R5 requires consecutive hours within complete days")
			}
			if j%24 > 0 && h.HourEnding != in.Hours[j-1].HourEnding+1 {
				return fmt.Errorf("incomplete day")
			}
		}
	}
	if p.InteriorConvection < .5 || p.InteriorConvection > 2 || p.ExteriorFilm < .5 || p.ExteriorFilm > 2 || p.SolarFloorFraction < .5 || p.SolarFloorFraction > .8 {
		return fmt.Errorf("parameters outside documented bounds")
	}
	if len(in.Surfaces) != 4 || len(in.Hours) == 0 {
		return fmt.Errorf("four surfaces and nonempty hours required")
	}
	names := []string{"wall", "roof", "floor", "window"}
	for j, s := range in.Surfaces {
		if s.Name != names[j] || s.Area <= 0 || s.U < 0 || s.Resistance < 0 || s.Hi <= 0 || s.Ho <= 0 || s.SkyView < 0 || s.SkyView > 1 || s.Absorptance < 0 || s.Absorptance > 1 {
			return fmt.Errorf("invalid surface %d", j)
		}
	}
	prev := 0
	for _, h := range in.Hours {
		if h.HourEnding <= prev || h.HourEnding > 8760 || len(h.BoundaryC) != 4 || len(h.Irradiance) != 4 || h.HeatSetpoint >= h.CoolSetpoint || h.AirC <= -273.15 || h.SkyIR < 0 || h.SolarTransmitted < 0 || h.SolarAbsorbed < 0 || h.Internal < 0 {
			return fmt.Errorf("invalid hour %d", h.HourEnding)
		}
		for j := 0; j < 4; j++ {
			if h.Irradiance[j] < 0 || h.BoundaryC[j] <= -273.15 {
				return fmt.Errorf("invalid surface boundary at %d", h.HourEnding)
			}
		}
		prev = h.HourEnding
	}
	return nil
}

func balance(in Input, h Hour, tin float64) Balance {
	b := Balance{AirC: tin, SurfaceC: make([]float64, 4), SurfaceToAir: make([]float64, 4), BoundaryToSurface: make([]float64, 4), ContactTransfer: make([]float64, 4)}
	b.Infiltration = in.Density * in.CP * in.Volume * in.ACH / 3600 * (h.AirC - tin)
	b.AbsorbedSolar = .965 * h.SolarTransmitted
	b.DirectSolar = h.SolarAbsorbed
	p := in.Parameters
	floor := p.SolarFloorFraction
	f := []float64{(.965 - floor) * .155 / .323, (.965 - floor) * .168 / .323, floor, 0}
	var g, hi, te, base, den, rad [4]float64
	area, mrtNum, mrtDen := 0., 0., 0.
	for _, s := range in.Surfaces {
		area += s.Area
	}
	const hr = 5.13
	for j, s := range in.Surfaces {
		ho := s.Ho * p.ExteriorFilm
		hi[j] = s.Hi * p.InteriorConvection
		ext := s.Absorptance*h.Irradiance[j] - .9*s.SkyView*(5.670374419e-8*math.Pow(h.AirC+273.15, 4)-h.SkyIR)
		te[j] = h.BoundaryC[j] + ext/ho
		if in.Stage == "R2" {
			b.SurfaceToAir[j] = s.Area * s.U * (te[j] - tin)
			b.BoundaryToSurface[j] = b.SurfaceToAir[j]
			b.ContactTransfer[j] = s.Area * s.U * (h.BoundaryC[j] - tin)
			b.SurfaceC[j] = tin + b.SurfaceToAir[j]/(s.Area*hi[j])
			continue
		}
		g[j] = 1 / (s.Resistance + 1/ho)
		if s.U == 0 {
			g[j] = 0
		} // explicitly adiabatic; never fall back to wall U
		rad[j] = h.SolarTransmitted*f[j] + h.Internal*in.RadiantFraction*s.Area/area
		den[j] = g[j] + hi[j] + hr
		base[j] = (g[j]*te[j] + hi[j]*tin + rad[j]/s.Area) / den[j]
		mrtNum += s.Area * base[j]
		mrtDen += s.Area * (1 - hr/den[j])
	}
	if in.Stage == "R2" {
		b.DirectInternal = h.Internal
		b.DirectSolar += b.AbsorbedSolar
	} else {
		b.DirectInternal = h.Internal * (1 - in.RadiantFraction)
		b.RadiantInternal = h.Internal * in.RadiantFraction
		mrt := mrtNum / mrtDen
		for j, s := range in.Surfaces {
			b.SurfaceC[j] = base[j] + hr/den[j]*mrt
			b.SurfaceToAir[j] = s.Area * hi[j] * (b.SurfaceC[j] - tin)
			b.BoundaryToSurface[j] = s.Area * g[j] * (te[j] - b.SurfaceC[j])
			// Signed temperature-driven exchange only; exclude sol-air radiation.
			b.ContactTransfer[j] = s.Area * g[j] * (h.BoundaryC[j] - b.SurfaceC[j])
			b.Residual += b.BoundaryToSurface[j] + rad[j] - b.SurfaceToAir[j]
		}
	}
	b.Net = b.Infiltration + b.DirectInternal + b.DirectSolar
	for _, q := range b.SurfaceToAir {
		b.Net += q
	}
	return b
}

// Redistribute one channel without changing its daily integral. All parameters
// are global; centroid is derived from that day's actual input, not a case ID.
func distribute(x [24]float64, fraction float64, r Redistribution) [24]float64 {
	var y, w [24]float64
	energy, weighted := 0., 0.
	for j, v := range x {
		energy += v
		weighted += v * float64(j+1)
	}
	if energy == 0 || fraction == 0 {
		return x
	}
	center := weighted/energy + r.DelayHours
	sum := 0.
	for j := range w {
		w[j] = math.Exp(-.5 * math.Pow((float64(j+1)-center)/r.WidthHours, 2))
		sum += w[j]
	}
	for j := range y {
		weight := (1-r.BackgroundFraction)*w[j]/sum + r.BackgroundFraction/24
		y[j] = (1-fraction)*x[j] + fraction*energy*weight
	}
	return y
}
func redistribution(in Input) ([]Hour, []DayAudit) {
	hours := append([]Hour(nil), in.Hours...)
	var audits []DayAudit
	if in.Stage != "R5" {
		return hours, audits
	}
	for j := range hours {
		hours[j].Irradiance = append([]float64(nil), hours[j].Irradiance...)
	}
	for start := 0; start < len(hours); start += 24 {
		a := DayAudit{Day: (hours[start].HourEnding-1)/24 + 1, OpaqueBefore: make([]float64, 4), OpaqueAfter: make([]float64, 4)}
		var x [24]float64
		for j := range x {
			x[j] = in.Hours[start+j].SolarTransmitted
			a.WindowBefore += x[j]
		}
		y := distribute(x, in.Redistribution.WindowFraction, in.Redistribution)
		for j, v := range y {
			hours[start+j].SolarTransmitted = v
			a.WindowAfter += v
		}
		for s := 0; s < 4; s++ {
			scale := in.Surfaces[s].Area * in.Surfaces[s].Absorptance
			for j := range x {
				x[j] = in.Hours[start+j].Irradiance[s]
				a.OpaqueBefore[s] += x[j] * scale
			}
			y = distribute(x, in.Redistribution.OpaqueFraction, in.Redistribution)
			for j, v := range y {
				hours[start+j].Irradiance[s] = v
				a.OpaqueAfter[s] += v * scale
			}
		}
		audits = append(audits, a)
	}
	return hours, audits
}
func simulate(in Input) Output {
	o := Output{Protocol: in.Protocol, ResolvedInput: in, Totals: map[string]float64{"heating_kwh": 0, "cooling_kwh": 0, "electricity_kwh": 0}}
	o.EffectiveHours, o.DailyRedistribution = redistribution(in)
	for _, h := range o.EffectiveHours {
		bc, bh := balance(in, h, h.CoolSetpoint), balance(in, h, h.HeatSetpoint)
		r := Result{HourEnding: h.HourEnding, Cooling: math.Max(0, bc.Net) / 1000, Heating: math.Max(0, -bh.Net) / 1000}
		if r.Cooling > 0 {
			r.Balance = bc
		} else if r.Heating > 0 {
			r.Balance = bh
		} else {
			// The balance is affine in air temperature; solve the free floating deadband.
			tf := (h.HeatSetpoint + h.CoolSetpoint) / 2
			if math.Abs(bc.Net-bh.Net) > 1e-12 {
				tf = h.HeatSetpoint - bh.Net*(h.CoolSetpoint-h.HeatSetpoint)/(bc.Net-bh.Net)
			}
			r.Balance = balance(in, h, tf)
		}
		r.Signed = r.Heating - r.Cooling
		r.Electricity = r.Cooling/in.CoolingCOP + r.Heating/in.HeatingCOP
		o.Hours = append(o.Hours, r)
		o.Totals["heating_kwh"] += r.Heating
		o.Totals["cooling_kwh"] += r.Cooling
		o.Totals["electricity_kwh"] += r.Electricity
	}
	return o
}
func run() error {
	if len(os.Args) != 3 {
		return fmt.Errorf("usage: MoosasEnergy input.json output.json")
	}
	raw, err := os.ReadFile(os.Args[1])
	if err != nil {
		return err
	}
	if err = requireFields(raw, reflect.TypeOf(Input{}), "input"); err != nil {
		return err
	}
	var in Input
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.DisallowUnknownFields()
	if err = dec.Decode(&in); err != nil {
		return err
	}
	var extra any
	if err = dec.Decode(&extra); err != io.EOF {
		return fmt.Errorf("trailing input")
	}
	if err = validate(in); err != nil {
		return err
	}
	data, err := json.MarshalIndent(simulate(in), "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(os.Args[2], append(data, '\n'), 0644)
}
func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
