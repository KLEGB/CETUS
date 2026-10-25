package main

import (
	"math"
	"reflect"
	"testing"
)

func example() Input {
	s := []Surface{{Name: "wall", Kind: "wall", Area: 50, R: 2, Mass: 80000, Rho: .3, Eps: .9, Hi: 3, Ho: 10, Sky: .5, Ground: .5},
		{Name: "window", Kind: "window", Area: 10, R: .3, Rho: .2, Tau: .6, Eps: .84, Hi: 3, Ho: 10, Sky: .5, Ground: .5},
		{Name: "floor", Kind: "floor", Area: 30, R: 2, Mass: 120000, Rho: 1, Eps: .9, Hi: 3, GroundH: 100}}
	h := Hour{Air: 20, Ground: 20, SkyIR: sigma * fourth(20), Irr: []float64{0, 0, 0}, Heat: 18, Cool: 26, Vent: 20}
	return Input{Protocol: "cetus-surface-7", Dt: 3600, Surfaces: s, Hours: []Hour{h}, InitialC: 20, COPc: 3, COPh: 3, Radiant: .6, SolarFloor: .642}
}
func TestEquilibrium(t *testing.T) {
	p := example()
	o, e := run(p)
	if e != nil {
		t.Fatal(e)
	}
	h := o.Hours[0]
	if math.Abs(h.Zone-20) > 1e-8 || math.Abs(h.HVAC) > 1e-8 || math.Abs(h.Storage) > 1e-7 {
		t.Fatal(h)
	}
}
func TestSolarBalanceAndZeroWindowStorage(t *testing.T) {
	p := example()
	p.Hours[0].Irr = []float64{600, 600, 0}
	p.Hours[0].Internal = 200
	o, e := run(p)
	if e != nil {
		t.Fatal(e)
	}
	h := o.Hours[0]
	if math.Abs(h.Residual) > 1e-5 || h.Surfaces[1].Storage != 0 || h.Surfaces[1].Delta != 0 {
		t.Fatal(h)
	}
	if h.HVAC >= 0 {
		t.Fatal("expected cooling")
	}
}
func TestSplitAndImmutable(t *testing.T) {
	p := example()
	p.Hours[0].Irr = []float64{400, 400, 0}
	p.Hours = append(p.Hours, p.Hours[0])
	o, e := run(p)
	if e != nil {
		t.Fatal(e)
	}
	p.Hours = p.Hours[:1]
	first, e := run(p)
	if e != nil {
		t.Fatal(e)
	}
	p.Initial = first.Final
	old := append([]float64(nil), p.Initial...)
	last, e := run(p)
	if e != nil {
		t.Fatal(e)
	}
	if !reflect.DeepEqual(old, p.Initial) {
		t.Fatal("input state mutated")
	}
	for i, x := range o.Final {
		if math.Abs(x-last.Final[i]) > 1e-9 {
			t.Fatal("split differs")
		}
	}
	again, _ := run(p)
	if !reflect.DeepEqual(last, again) {
		t.Fatal("trial changed state")
	}
}
func TestRejectWindowMass(t *testing.T) {
	p := example()
	p.Surfaces[1].Mass = 1
	if _, e := run(p); e == nil {
		t.Fatal("accepted window mass")
	}
}
func TestRejectOptics(t *testing.T) {
	p := example()
	p.Surfaces[1].Rho = .7
	if _, e := run(p); e == nil {
		t.Fatal("accepted rho+tau>1")
	}
}
func TestZeroMassLimit(t *testing.T) {
	p := example()
	for i := range p.Surfaces {
		p.Surfaces[i].Mass = 0
	}
	p.Hours[0].Air = 30
	p.Hours[0].Irr = []float64{300, 300, 0}
	a, e := run(p)
	if e != nil {
		t.Fatal(e)
	}
	p.InitialC = 0
	b, e := run(p)
	if e != nil {
		t.Fatal(e)
	}
	if math.Abs(a.Hours[0].HVAC-b.Hours[0].HVAC) > 1e-6 {
		t.Fatal("zero mass depends on history")
	}
}
func TestHeatingBalance(t *testing.T) {
	p := example()
	p.Hours[0].Air = -5
	p.Hours[0].Ground = 5
	p.Hours[0].SkyIR = sigma * fourth(-10)
	o, e := run(p)
	if e != nil {
		t.Fatal(e)
	}
	h := o.Hours[0]
	if h.HVAC <= 0 || math.Abs(h.OutdoorHVAC-(-h.HVAC+h.Electricity)) > 1e-8 {
		t.Fatal(h)
	}
}
func TestLayeredConstruction(t *testing.T) {
	p := example()
	p.Surfaces[0].Layers = []Layer{{.2, 100000}, {1.8, 1000}}
	p.Hours[0].Irr = []float64{500, 0, 0}
	o, e := run(p)
	if e != nil {
		t.Fatal(e)
	}
	if math.Abs(o.Hours[0].Residual) > 1e-5 {
		t.Fatal(o)
	}
}
func TestTransparentAbsorption(t *testing.T) {
	p := example()
	p.Hours[0].Irr = []float64{0, 500, 0}
	p.Surfaces[1].AbsInside = .5
	o, e := run(p)
	if e != nil {
		t.Fatal(e)
	}
	w := o.Hours[0].Surfaces[1]
	if w.Absorbed <= 0 || w.Storage != 0 || math.Abs(w.Incident-w.Absorbed-w.Transmitted-w.Reflected) > 1e-8 {
		t.Fatal(w)
	}
}

func TestParallelBatchMatchesSerial(t *testing.T) {
	p := example()
	p.Hours[0].Irr = []float64{400, 400, 0}
	expected, e := run(p)
	if e != nil {
		t.Fatal(e)
	}
	inputs := make([]Input, 20)
	for i := range inputs {
		inputs[i] = p
	}
	result, e := runBatch(Request{Batch: inputs}, nil)
	if e != nil {
		t.Fatal(e)
	}
	for _, o := range result {
		if !reflect.DeepEqual(o, expected) {
			t.Fatal("parallel batch changed result")
		}
	}
}

func TestNetworkIRReplacement(t *testing.T) {
	p := example()
	p.Hours[0].NetworkIR = []float64{sigma * fourth(20), sigma * fourth(20), 0}
	// Change the unused legacy sky boundary while retaining the same contact soil.
	p.Hours[0].SkyIR = 1000
	o, e := run(p)
	if e != nil {
		t.Fatal(e)
	}
	h := o.Hours[0]
	if math.Abs(h.Zone-20) > 1e-8 || math.Abs(h.NetworkLW) > 1e-7 || h.SkyLW != 0 || h.GroundLW != 0 {
		t.Fatal(h)
	}
	p.Hours[0].NetworkIR = []float64{1}
	if _, e = run(p); e == nil {
		t.Fatal("accepted incorrect network IR array")
	}
}

func TestWindowLongwaveAbsorptionWithoutTransmission(t *testing.T) {
	p := example()
	p.Hours[0].Irr = []float64{0, 500, 0}
	p.Hours[0].NetworkIR = []float64{sigma * fourth(20), sigma * fourth(20), 0}
	cold, e := run(p)
	if e != nil {
		t.Fatal(e)
	}
	p.Hours[0].NetworkIR[1] = sigma * fourth(80)
	hot, e := run(p)
	if e != nil {
		t.Fatal(e)
	}
	a, b := cold.Hours[0].Surfaces[1], hot.Hours[0].Surfaces[1]
	if a.Transmitted != 3000 || b.Transmitted != a.Transmitted || a.Absorbed != b.Absorbed || a.Reflected != b.Reflected {
		t.Fatal("IR changed solar transmission", a, b)
	}
	if b.Te <= a.Te || b.Ti <= a.Ti || b.Storage != 0 || math.Abs(hot.Hours[0].Residual) > 1e-5 {
		t.Fatal("window IR absorption or balance failed", a, b)
	}
	p.Hours[0].Irr[1] = 0
	night, e := run(p)
	if e != nil {
		t.Fatal(e)
	}
	if night.Hours[0].Surfaces[1].Transmitted != 0 {
		t.Fatal("longwave transmitted through window")
	}
}
