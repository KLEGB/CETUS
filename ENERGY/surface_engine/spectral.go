package main

import (
	"fmt"
	"math"
	"sync"
)

type LWBand struct {
	Lower float64  `json:"lower_um"`
	Upper *float64 `json:"upper_um"`
	Eps   float64  `json:"emissivity"`
	table []float64
}

var bandTables sync.Map

const planckGridLo = 173.15
const planckGridStep = .05
const planckGridN = 5601

func planckTail(x float64) float64 {
	if x < .05 {
		return 1 - 15/math.Pow(math.Pi, 4)*(math.Pow(x, 3)/3-math.Pow(x, 4)/8+math.Pow(x, 5)/60-math.Pow(x, 7)/5040+math.Pow(x, 9)/272160)
	}
	if x > 745 {
		return 0
	}
	sum := 0.
	for n := 1.; n <= 160; n++ {
		term := math.Exp(-n*x) * (x*x*x/n + 3*x*x/(n*n) + 6*x/(n*n*n) + 6/(n*n*n*n))
		sum += term
	}
	return sum * 15 / math.Pow(math.Pi, 4)
}
func bandExact(t, lo float64, hi *float64) float64 {
	upper := 1.
	lower := 0.
	if hi != nil {
		upper = planckTail(14387.768775039337 / (*hi * t))
	}
	if lo > 0 {
		lower = planckTail(14387.768775039337 / (lo * t))
	}
	return sigma * math.Pow(t, 4) * (upper - lower)
}
func prepareBands(s *Surface) error {
	if len(s.LWBands) == 0 {
		return nil
	}
	if s.Kind == "floor" || s.Eps <= 0 {
		return fmt.Errorf("spectral exterior requires a nonfloor surface and positive carrier emissivity")
	}
	previous := 0.
	integrated := 0.
	for i := range s.LWBands {
		b := &s.LWBands[i]
		if !finite(b.Lower) || b.Lower != previous || !finite(b.Eps) || b.Eps < 0 || b.Eps > 1 {
			return fmt.Errorf("invalid longwave band")
		}
		if b.Upper == nil {
			if i != len(s.LWBands)-1 {
				return fmt.Errorf("infinite band must be last")
			}
		} else {
			if !finite(*b.Upper) || *b.Upper <= b.Lower {
				return fmt.Errorf("invalid upper wavelength")
			}
			previous = *b.Upper
		}
		key := fmt.Sprintf("%.17g/", b.Lower)
		if b.Upper == nil {
			key += "inf"
		} else {
			key += fmt.Sprintf("%.17g", *b.Upper)
		}
		cached, ok := bandTables.Load(key)
		if !ok {
			table := make([]float64, planckGridN)
			for j := range table {
				table[j] = bandExact(planckGridLo+float64(j)*planckGridStep, b.Lower, b.Upper)
			}
			cached, _ = bandTables.LoadOrStore(key, table)
		}
		b.table = cached.([]float64)
		integrated += b.Eps * bandExact(300, b.Lower, b.Upper) / (sigma * math.Pow(300, 4))
	}
	if s.LWBands[len(s.LWBands)-1].Upper != nil {
		return fmt.Errorf("longwave bands must span all wavelengths")
	}
	if integrated >= 1-1e-12 {
		return fmt.Errorf("integrated emissivity must be < 1")
	}
	return nil
}
func spectralEmission(s Surface, celsius float64) (float64, float64) {
	t := celsius + 273.15
	value := 0.
	derivative := 0.
	for _, b := range s.LWBands {
		pos := (t - planckGridLo) / planckGridStep
		j := int(math.Floor(pos))
		if j >= 0 && j < planckGridN-1 && len(b.table) == planckGridN {
			slope := (b.table[j+1] - b.table[j]) / planckGridStep
			value += b.Eps * (b.table[j] + slope*(t-(planckGridLo+float64(j)*planckGridStep)))
			derivative += b.Eps * slope
		} else {
			value += b.Eps * bandExact(t, b.Lower, b.Upper)
			derivative += b.Eps * (bandExact(t+.001, b.Lower, b.Upper) - bandExact(t-.001, b.Lower, b.Upper)) / .002
		}
	}
	return value, derivative
}
func exteriorNetworkLW(s Surface, h Hour, k int, temp float64) float64 {
	if len(s.LWBands) > 0 {
		e, _ := spectralEmission(s, temp)
		return s.Area * (e - s.Eps*h.NetworkIR[k])
	}
	return s.Area * s.Eps * (sigma*fourth(temp) - h.NetworkIR[k])
}
