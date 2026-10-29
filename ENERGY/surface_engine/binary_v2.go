package main

import "fmt"

// CES3 v2 registration has no forcing records. Per-surface role, boundary and
// inside emissivity are explicit; all remaining numeric fields retain SI units.
func readModelV2(r *reader) Input {
	p := Input{Protocol: "cetus-surface-8", ZoneID: r.text(), Dt: r.f(), InitialC: r.f(), COPc: r.f(), COPh: r.f(), Radiant: r.f(), SolarFloor: r.f()}
	n := int(r.u())
	if n < 1 || n > len(r.b)/100 {
		r.err = fmt.Errorf("invalid v2 surface count")
		return p
	}
	p.Surfaces = make([]Surface, n)
	for i := range p.Surfaces {
		s := Surface{Name: r.text(), SourceID: r.text(), Kind: r.text()}
		code := r.u()
		switch code {
		case 0:
			s.Boundary = "outdoors"
		case 1:
			s.Boundary = "ground"
		case 2:
			s.Boundary = "adiabatic"
		default:
			r.err = fmt.Errorf("invalid v2 boundary code")
		}
		s.Area = r.f()
		s.R = r.f()
		s.Mass = r.f()
		s.Rho = r.f()
		s.Tau = r.f()
		s.Eps = r.f()
		s.Hi = r.f()
		s.Ho = r.f()
		s.Sky = r.f()
		s.Ground = r.f()
		s.GroundH = r.f()
		s.AbsInside = r.f()
		eps := r.f()
		s.InsideEps = &eps
		nl := int(r.u())
		if nl > (len(r.b)-r.p)/16 {
			r.err = fmt.Errorf("invalid layer count")
			return p
		}
		s.Layers = make([]Layer, nl)
		for j := range s.Layers {
			s.Layers[j] = Layer{R: r.f(), C: r.f()}
		}
		p.Surfaces[i] = s
	}
	return p
}
