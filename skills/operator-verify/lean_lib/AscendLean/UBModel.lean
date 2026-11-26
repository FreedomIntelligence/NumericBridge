/-
  AscendLean.UBModel
  Formal Unified Buffer (UB) capacity, tiling, int32/int64 address bounds,
  and AscendC packed vector mask model for Ascend A3 (192 KiB) and A5 / 950PR (248 KiB).
-/

namespace AscendLean.UBModel

def A3_UB_BITS : Nat := 192 * 1024 * 8
def A5_UB_BITS : Nat := 2031616  -- 248 KiB, exact compiler limit on Arch35 (A5)

def INT32_MAX : Nat := 2147483647          -- 2^31 - 1
def INT64_MAX : Nat := 9223372036854775807 -- 2^63 - 1

def ceilDiv (a b : Nat) : Nat :=
  if b = 0 then 0 else (a + b - 1) / b

/-- Universal ceiling-division coverage bound: for any `b > 0`, `a ≤ ceilDiv a b * b`. -/
theorem le_ceilDiv_mul (a b : Nat) (hb : 0 < b) : a ≤ ceilDiv a b * b := by
  unfold ceilDiv
  have hne : b ≠ 0 := by omega
  simp only [hne, ↓reduceIte]
  have hmod := Nat.div_add_mod (a + b - 1) b
  have hlt := Nat.mod_lt (a + b - 1) hb
  have hcomm : (a + b - 1) / b * b = b * ((a + b - 1) / b) := Nat.mul_comm _ _
  omega

/-- Unbounded power-of-two ceiling `nextPow2 n`: smallest power of two `≥ n` (with `nextPow2 0 = 1`). -/
def nextPow2Aux (n acc : Nat) : Nat → Nat
  | 0 => acc
  | fuel + 1 => if n ≤ acc then acc else nextPow2Aux n (2 * acc) fuel

def nextPow2 (n : Nat) : Nat :=
  nextPow2Aux n 1 n

private theorem mul_two_pow_succ (acc k : Nat) :
    2 * acc * 2 ^ k = acc * 2 ^ (k + 1) := by
  rw [Nat.pow_succ, Nat.mul_comm 2 acc, Nat.mul_assoc, Nat.mul_comm 2 (2 ^ k), ← Nat.mul_assoc]

private theorem nextPow2Aux_ge (n acc fuel : Nat) (h : n ≤ acc * 2 ^ fuel) :
    n ≤ nextPow2Aux n acc fuel := by
  induction fuel generalizing acc with
  | zero =>
      simp only [nextPow2Aux, Nat.pow_zero, Nat.mul_one] at *
      exact h
  | succ k ih =>
      simp only [nextPow2Aux]
      by_cases hna : n ≤ acc
      · simp only [hna, ↓reduceIte]
      · simp only [hna, ↓reduceIte]
        apply ih
        rw [mul_two_pow_succ]
        exact h

private theorem le_two_pow (n : Nat) : n ≤ 2 ^ n := by
  induction n with
  | zero => decide
  | succ k ih =>
      rw [Nat.pow_succ]
      have hpos : 1 ≤ 2 ^ k := Nat.one_le_two_pow
      omega

/-- Universal guarantee: `nextPow2 n ≥ n` for EVERY `n : Nat` (no silent clamp). -/
theorem le_nextPow2 (n : Nat) : n ≤ nextPow2 n := by
  unfold nextPow2
  apply nextPow2Aux_ge
  simpa using le_two_pow n

private theorem nextPow2Aux_le_pow2 (n acc bound fuel : Nat)
    (hn : n ≤ bound) (hacc : acc ≤ bound) (hdbl : ∀ k, acc * 2 ^ k < bound → acc * 2 ^ (k + 1) ≤ bound) :
    nextPow2Aux n acc fuel ≤ bound := by
  induction fuel generalizing acc with
  | zero => exact hacc
  | succ k ih =>
      simp only [nextPow2Aux]
      by_cases hna : n ≤ acc
      · simp only [hna, ↓reduceIte]; exact hacc
      · simp only [hna, ↓reduceIte]
        have hlt : acc < bound := by omega
        have h2le : 2 * acc ≤ bound := by
          have := hdbl 0 (by simpa using hlt)
          simpa [Nat.mul_comm] using this
        apply ih (2 * acc) h2le
        intro m hm
        rw [mul_two_pow_succ] at hm
        rw [mul_two_pow_succ]
        exact hdbl (m + 1) hm

private theorem pow2_step_le (p k : Nat) (hlt : 2 ^ k < 2 ^ p) : 2 ^ (k + 1) ≤ 2 ^ p := by
  by_cases hkp : k < p
  · exact Nat.pow_le_pow_right (by decide) hkp
  · have hle : p ≤ k := Nat.le_of_not_lt hkp
    have hpow : 2 ^ p ≤ 2 ^ k := Nat.pow_le_pow_right (by decide) hle
    omega

theorem nextPow2_le_pow2 (n p : Nat) (h : n ≤ 2 ^ p) : nextPow2 n ≤ 2 ^ p := by
  unfold nextPow2
  apply nextPow2Aux_le_pow2 n 1 (2 ^ p) n h Nat.one_le_two_pow
  intro k hk
  simp only [Nat.one_mul] at hk ⊢
  exact pow2_step_le p k hk

theorem nextPow2_le_4096 (n : Nat) (h : n ≤ 4096) : nextPow2 n ≤ 4096 :=
  nextPow2_le_pow2 n 12 h

theorem nextPow2_le_8192 (n : Nat) (h : n ≤ 8192) : nextPow2 n ≤ 8192 :=
  nextPow2_le_pow2 n 13 h

/-- Exact AscendC `UpdateMask<float>(rem)` semantics for one 256-byte vector register. -/
def updateMask (rem vl : Nat) : Nat × Nat :=
  let active := min rem vl
  (active, rem - active)

/-- Four consecutive packed stores sharing ONE counter `rem`. -/
def stepPacked4Shared (rem vl : Nat) : Nat × Nat :=
  let (w0, r1) := updateMask rem vl
  let (w1, r2) := updateMask r1 vl
  let (w2, r3) := updateMask r2 vl
  let (w3, r4) := updateMask r3 vl
  (w0 + w1 + w2 + w3, r4)

/-- Four consecutive packed stores with FOUR independent counters. -/
def stepPacked4Indep (r0 r1 r2 r3 vl : Nat) : Nat × (Nat × Nat × Nat × Nat) :=
  let (w0, r0') := updateMask r0 vl
  let (w1, r1') := updateMask r1 vl
  let (w2, r2') := updateMask r2 vl
  let (w3, r3') := updateMask r3 vl
  (w0 + w1 + w2 + w3, (r0', r1', r2', r3'))

theorem stepPacked4Shared_eq (rem vl : Nat) :
    stepPacked4Shared rem vl = (min rem (4 * vl), rem - min rem (4 * vl)) := by
  simp only [stepPacked4Shared, updateMask, Prod.mk.injEq]
  omega

def loopPacked4Shared (loops rem vl : Nat) : Nat :=
  match loops with
  | 0 => 0
  | n + 1 =>
      let (w, rem') := stepPacked4Shared rem vl
      w + loopPacked4Shared n rem' vl

def loopPacked4Indep (loops r0 r1 r2 r3 vl : Nat) : Nat :=
  match loops with
  | 0 => 0
  | n + 1 =>
      let (w, (r0', r1', r2', r3')) := stepPacked4Indep r0 r1 r2 r3 vl
      w + loopPacked4Indep n r0' r1' r2' r3' vl

theorem loopPacked4Shared_closed (loops rem vl : Nat) :
    loopPacked4Shared loops rem vl = min rem (loops * (4 * vl)) := by
  induction loops generalizing rem with
  | zero => simp [loopPacked4Shared]
  | succ n ih =>
      simp only [loopPacked4Shared, stepPacked4Shared_eq]
      rw [ih]
      have hmul : (n + 1) * (4 * vl) = 4 * vl + n * (4 * vl) := by
        rw [Nat.add_mul, Nat.one_mul, Nat.add_comm]
      omega

theorem loopPacked4Shared_exact (curColNum vl : Nat) (hvl : 0 < vl) :
    loopPacked4Shared (ceilDiv curColNum (4 * vl)) curColNum vl = curColNum := by
  rw [loopPacked4Shared_closed]
  have hge := le_ceilDiv_mul curColNum (4 * vl) (by omega)
  omega

/-- Round `bytes` up to the nearest 32-byte Ascend UB alignment boundary. -/
def alignUp32 (bytes : Nat) : Nat :=
  ceilDiv bytes 32 * 32

theorem alignUp32_ge (bytes : Nat) : bytes ≤ alignUp32 bytes :=
  le_ceilDiv_mul bytes 32 (by decide)

theorem alignUp32_mod32 (bytes : Nat) : alignUp32 bytes % 32 = 0 := by
  unfold alignUp32
  exact Nat.mul_mod_left _ 32

/-- Two half-open UB byte intervals `[s1, e1)` and `[s2, e2)` are disjoint. -/
abbrev ubDisjoint (s1 e1 s2 e2 : Nat) : Prop :=
  e1 ≤ s2 ∨ e2 ≤ s1

/-- Hardware constraint for AscendC `Cast(dst, src, count)` inside a UB buffer of `bufBytes`:
    both ranges stay in bounds, are 32-byte aligned, and the source and destination byte ranges
    must be strictly disjoint unless `srcWidth = dstWidth` and they start at the exact same offset
    (partial overlap is forbidden even for equal-width casts). -/
abbrev ubCastSafe (bufBytes srcOff dstOff count srcWidth dstWidth : Nat) : Prop :=
  srcOff % 32 = 0 ∧
  dstOff % 32 = 0 ∧
  srcOff + count * srcWidth ≤ bufBytes ∧
  dstOff + count * dstWidth ≤ bufBytes ∧
  ((srcWidth = dstWidth ∧ srcOff = dstOff) ∨
    ubDisjoint srcOff (srcOff + count * srcWidth) dstOff (dstOff + count * dstWidth))

end AscendLean.UBModel
