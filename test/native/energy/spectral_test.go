package main

import (
	"math"
	"testing"
)

func TestSpectralPlanckAndEmission(t *testing.T) {
	a, b := 8., 13.
	s := Surface{Area: 10, Kind: "roof", Eps: .9, LWBands: []LWBand{{Lower: 0, Upper: &a, Eps: .1}, {Lower: 8, Upper: &b, Eps: .95}, {Lower: 13, Upper: nil, Eps: .1}}}
	if e := prepareBands(&s); e != nil {
		t.Fatal(e)
	}
	// Independent value from adaptive quadrature, checked by Python against scipy.quad.
	f := bandExact(300, 8, &b) / (sigma * math.Pow(300, 4))
	if math.Abs(f-.32215317034292645) > 1e-8 {
		t.Fatalf("window fraction %g", f)
	}
	v, g := spectralEmission(s, 26.85)
	want := .1*sigma*math.Pow(300, 4) + .85*bandExact(300, 8, &b)
	if math.Abs(v-want) > 1e-4 || g <= 0 {
		t.Fatalf("spectral value %g want %g derivative %g", v, want, g)
	}
	h := Hour{NetworkIR: []float64{100}}
	if math.Abs(exteriorNetworkLW(s, h, 0, 26.85)-s.Area*(v-.9*100)) > 1e-8 {
		t.Fatal("net LW disagrees")
	}
}
func TestRejectOverlappingSpectralBands(t *testing.T) {
	a := 8.
	s := Surface{Kind: "wall", Eps: .9, LWBands: []LWBand{{Lower: 0, Upper: &a, Eps: .8}, {Lower: 7, Upper: nil, Eps: .8}}}
	if prepareBands(&s) == nil {
		t.Fatal("accepted overlapping bands")
	}
}
