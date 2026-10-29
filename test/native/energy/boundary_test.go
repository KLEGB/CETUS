package main

import (
	"bytes"
	"math"
	"reflect"
	"testing"
)

func insulatedExample() Input {
	p := example()
	p.Protocol = "cetus-surface-8"
	p.ZoneID = "core"
	p.Surfaces = p.Surfaces[:1]
	p.Surfaces[0].Boundary = "adiabatic"
	p.Surfaces[0].Ho = 0
	p.Surfaces[0].Sky = 0
	p.Surfaces[0].Ground = 0
	p.Hours[0].Irr = []float64{0}
	p.Hours[0].Vent = 0
	p.Hours[0].Internal = 200
	disabled := false
	p.Hours[0].HVACEnabled = &disabled
	return p
}

func TestAdiabaticStorageAndWeatherIndependence(t *testing.T) {
	p := insulatedExample()
	a, err := run(p)
	if err != nil {
		t.Fatal(err)
	}
	p.Hours[0].Air = -40
	p.Hours[0].Ground = 60
	p.Hours[0].SkyIR = 1000
	b, err := run(p)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(a.Final, b.Final) {
		t.Fatal("adiabatic zone depends on weather")
	}
	h := a.Hours[0]
	s := h.Surfaces[0]
	if h.HVAC != 0 || s.ExteriorConvection != 0 || s.ExteriorContact != 0 || s.ExternalLW != 0 {
		t.Fatal(h)
	}
	if math.Abs(h.Storage-200) > 1e-6 || math.Abs(h.Residual) > 1e-6 {
		t.Fatal(h)
	}
}

func TestFloorRoleIndependentOfBoundary(t *testing.T) {
	for _, kind := range []string{"floor", "ceiling", "wall"} {
		p := insulatedExample()
		p.Surfaces[0].Kind = kind
		if _, err := run(p); err != nil {
			t.Fatal(kind, err)
		}
		p.Surfaces[0].Boundary = "outdoors"
		p.Surfaces[0].Ho = 10
		p.Surfaces[0].Sky = .5
		p.Surfaces[0].Ground = .5
		if _, err := run(p); err != nil {
			t.Fatal(kind, err)
		}
	}
}

func TestAdiabaticRejectsExteriorForcing(t *testing.T) {
	p := insulatedExample()
	p.Hours[0].Irr[0] = 1
	if _, err := run(p); err == nil {
		t.Fatal("accepted exterior solar on insulated surface")
	}
}

func TestZeroIndoorEmissivityWithoutRadiantGains(t *testing.T) {
	p := insulatedExample()
	p.Radiant = 0
	zero := 0.
	p.Surfaces[0].InsideEps = &zero
	o, err := run(p)
	if err != nil {
		t.Fatal(err)
	}
	if math.Abs(o.Hours[0].Storage-200) > 1e-6 {
		t.Fatal(o.Hours[0])
	}
	p.Radiant = .6
	if _, err := run(p); err == nil {
		t.Fatal("accepted radiant gains with no absorbing inside surface")
	}
}

func putTextV2(b *bytes.Buffer, s string) { putU(b, uint32(len(s))); b.WriteString(s) }
func TestV2RegistrationAndExplicitHVACFlag(t *testing.T) {
	p := insulatedExample()
	s := p.Surfaces[0]
	b := new(bytes.Buffer)
	putU(b, 1)
	putTextV2(b, p.ZoneID)
	for _, v := range []float64{p.Dt, p.InitialC, p.COPc, p.COPh, p.Radiant, p.SolarFloor} {
		putF(b, v)
	}
	putU(b, 1)
	putTextV2(b, s.Name)
	putTextV2(b, "source")
	putTextV2(b, s.Kind)
	putU(b, 2)
	for _, v := range []float64{s.Area, s.R, s.Mass, s.Rho, s.Tau, s.Eps, s.Hi, s.Ho, s.Sky, s.Ground, s.GroundH, s.AbsInside, s.Eps} {
		putF(b, v)
	}
	putU(b, 0)
	session := &binarySession{version: 2, warmStart: true, cacheEnabled: true}
	if _, err := session.handle(1, b.Bytes()); err != nil {
		t.Fatal(err)
	}
	step := new(bytes.Buffer)
	putID(step, 1)
	putU(step, 1)
	putArray(step, nil)
	h := p.Hours[0]
	for _, v := range []float64{h.Air, h.Ground, h.SkyIR, h.Internal, h.Heat, h.Cool, h.Vent, 0, 0} {
		putF(step, v)
	}
	putArray(step, h.Irr)
	putArray(step, nil)
	putArray(step, nil)
	putU(step, 0)
	if _, err := session.handle(2, step.Bytes()); err != nil {
		t.Fatal(err)
	}
	eval := new(bytes.Buffer)
	putID(eval, 1)
	putU(eval, 3)
	putU(eval, 1)
	putU(eval, 0)
	putU(eval, 0)
	first, err := session.handle(7, eval.Bytes())
	if err != nil {
		t.Fatal(err)
	}
	again, err := session.handle(7, eval.Bytes())
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(first[8:], again[8:]) {
		t.Fatal("trial advanced state")
	}
	if session.models[0].lastOut.HVAC != 0 {
		t.Fatal("ignored explicit disabled HVAC")
	}
}
