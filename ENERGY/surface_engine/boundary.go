package main

// Role and boundary are independent in the v2 contract. Empty boundary is only
// the historical v1 convention used by existing CETUS applications.
func boundaryKind(s Surface) string {
	if s.Boundary != "" {
		return s.Boundary
	}
	if s.Kind == "floor" {
		return "ground"
	}
	return "outdoors"
}

func insideEmissivity(s Surface) float64 {
	if s.InsideEps != nil {
		return *s.InsideEps
	}
	return s.Eps
}

func externalFilm(s Surface, h Hour, k int) float64 {
	if boundaryKind(s) != "outdoors" {
		return 0
	}
	if len(h.Ho) > 0 {
		return h.Ho[k]
	}
	return s.Ho
}

func hvacEnabled(h Hour) bool { return h.HVACEnabled == nil || *h.HVACEnabled }
