/-
  AscendLean.TritonModel
  Reusable formal semantics for Triton-Ascend kernels:
  1. BitVec 64 bitwise selection (CANN 9.1 int64 select workaround).
  2. Extended values with `-inf` & tiled 1D reduction equivalence.
  3. Online Softmax IEEE-754 abstract domain (`(-inf) - (-inf) = NaN` poisoning).
  4. Masked load padding / uninitialized UB taint & negative-index OOB guard semantics.
-/

namespace AscendLean.TritonModel

/-! ### 1. BitVec 64 Bitwise Select -/

def bitMask64 (cond : Bool) : BitVec 64 :=
  if cond then BitVec.ofInt 64 (-1) else 0#64

def bitwiseSelect64 (cond : Bool) (token value : BitVec 64) : BitVec 64 :=
  let sel := bitMask64 cond
  (token &&& sel) ||| (value &&& ~~~sel)

theorem bitMask64_true_eq_allOnes : BitVec.ofInt 64 (-1) = BitVec.allOnes 64 := by
  decide

theorem bitMask64_not_zero : ~~~(0#64) = BitVec.allOnes 64 := by
  decide

theorem bitwiseSelect64_eq_ite (cond : Bool) (token value : BitVec 64) :
    bitwiseSelect64 cond token value = if cond then token else value := by
  cases cond
  · simp only [bitwiseSelect64, bitMask64, Bool.false_eq_true, ↓reduceIte,
      bitMask64_not_zero, BitVec.and_zero, BitVec.and_allOnes, BitVec.zero_or]
  · simp only [bitwiseSelect64, bitMask64, ↓reduceIte,
      bitMask64_true_eq_allOnes, BitVec.not_allOnes, BitVec.and_allOnes, BitVec.and_zero, BitVec.or_zero]

/-! ### 2. Extended Order with `-inf` & Tiled Reduction -/

inductive ExtInt where
  | negInf
  | finite (v : Int)
  deriving DecidableEq, Repr

def ExtInt.gt (a b : ExtInt) : Bool :=
  match a, b with
  | .negInf, _ => false
  | .finite _, .negInf => true
  | .finite x, .finite y => decide (x > y)

def ExtInt.ge (a b : ExtInt) : Bool :=
  match a, b with
  | .negInf, .negInf => true
  | .negInf, .finite _ => false
  | .finite _, .negInf => true
  | .finite x, .finite y => decide (x >= y)

def countRange (p : Nat → Bool) : Nat → Nat
  | 0 => 0
  | n + 1 => countRange p n + if p n then 1 else 0

def sumBlock (f : Nat → Nat) (start : Nat) : Nat → Nat
  | 0 => 0
  | k + 1 => sumBlock f start k + f (start + k)

def sumTiles (f : Nat → Nat) (blockSize : Nat) : Nat → Nat
  | 0 => 0
  | b + 1 => sumTiles f blockSize b + sumBlock f (b * blockSize) blockSize

theorem sumBlock_eq_count (p : Nat → Bool) (start k : Nat) :
    sumBlock (fun idx => if p idx then 1 else 0) start k + countRange p start =
    countRange p (start + k) := by
  induction k with
  | zero => simp [sumBlock]
  | succ m ih =>
      simp only [sumBlock]
      have hstep : start + (m + 1) = (start + m) + 1 := by omega
      rw [hstep, countRange]
      omega

theorem sumTiles_eq_countRange (p : Nat → Bool) (blockSize numBlocks : Nat) :
    sumTiles (fun idx => if p idx then 1 else 0) blockSize numBlocks =
    countRange p (numBlocks * blockSize) := by
  induction numBlocks with
  | zero => simp [sumTiles, countRange]
  | succ b ih =>
      simp only [sumTiles, ih]
      have h := sumBlock_eq_count p (b * blockSize) blockSize
      have hmul : (b + 1) * blockSize = b * blockSize + blockSize := by
        rw [Nat.add_mul, Nat.one_mul]
      rw [hmul]
      omega

theorem countRange_zero_tail (p : Nat → Bool) (n m : Nat) (hnm : n ≤ m)
    (hzero : ∀ j, n ≤ j → j < m → p j = false) :
    countRange p m = countRange p n := by
  induction m with
  | zero =>
      have : n = 0 := by omega
      subst this; rfl
  | succ k ih =>
      by_cases hnk : n ≤ k
      · simp only [countRange]
        have hf : p k = false := hzero k hnk (by omega)
        rw [hf]
        simp only [Bool.false_eq_true, ↓reduceIte, Nat.add_zero]
        exact ih hnk (fun j hj1 hj2 => hzero j hj1 (by omega))
      · have : n = k + 1 := by omega
        subst this; rfl

/-! ### 3. Online Softmax IEEE-754 Abstract Domain (`(-inf) - (-inf) = NaN`) -/

inductive SumVal where
  | zero
  | pos
  | posInf
  | nan
  deriving DecidableEq, Repr

inductive MaxVal where
  | negInf
  | finite
  deriving DecidableEq, Repr

abbrev TileHasFinite := Bool

def expDiff (a b : MaxVal) : SumVal :=
  match a, b with
  | .negInf, .negInf => .nan
  | .negInf, .finite => .zero
  | .finite, .finite => .pos
  | .finite, .negInf => .posInf

def sumMul (x y : SumVal) : SumVal :=
  match x, y with
  | .nan, _ => .nan
  | _, .nan => .nan
  | .zero, .posInf => .nan
  | .posInf, .zero => .nan
  | .zero, _ => .zero
  | _, .zero => .zero
  | .posInf, _ => .posInf
  | _, .posInf => .posInf
  | .pos, .pos => .pos

def sumAdd (x y : SumVal) : SumVal :=
  match x, y with
  | .nan, _ => .nan
  | _, .nan => .nan
  | .posInf, _ => .posInf
  | _, .posInf => .posInf
  | .zero, b => b
  | a, .zero => a
  | .pos, .pos => .pos

structure OnlineState where
  m : MaxVal := .negInf
  s : SumVal := .zero
  deriving DecidableEq, Repr

def onlineStepBuggy (st : OnlineState) (tileFinite : TileHasFinite) : OnlineState :=
  let chunkMax : MaxVal := if tileFinite then .finite else .negInf
  let newM : MaxVal := match st.m, chunkMax with
    | .finite, _ => .finite
    | _, .finite => .finite
    | .negInf, .negInf => .negInf
  let scale := expDiff st.m newM
  let tileSum := expDiff chunkMax newM
  { m := newM, s := sumAdd (sumMul st.s scale) tileSum }

def onlineStepFixed (st : OnlineState) (tileFinite : TileHasFinite) : OnlineState :=
  let chunkMax : MaxVal := if tileFinite then .finite else .negInf
  let newM : MaxVal := match st.m, chunkMax with
    | .finite, _ => .finite
    | _, .finite => .finite
    | .negInf, .negInf => .negInf
  let safeM : MaxVal := .finite
  let scale := expDiff st.m safeM
  let tileSum := expDiff chunkMax safeM
  { m := newM, s := sumAdd (sumMul st.s scale) tileSum }

def runOnline (step : OnlineState → TileHasFinite → OnlineState)
    (st : OnlineState) (tiles : List TileHasFinite) : OnlineState :=
  tiles.foldl step st

theorem runOnline_buggy_dense_safe (rest : List TileHasFinite) :
    (runOnline onlineStepBuggy {} (true :: rest)).s = .pos := by
  have hgen : ∀ (ts : List TileHasFinite) (st : OnlineState),
      st.m = .finite → st.s = .pos → (runOnline onlineStepBuggy st ts).s = .pos := by
    intro ts
    induction ts with
    | nil => intro st _ hs; exact hs
    | cons b bs ih =>
        intro st hm hs
        cases b
        · exact ih _ (by simp [onlineStepBuggy, hm]) (by simp [onlineStepBuggy, hm, hs, expDiff, sumMul, sumAdd])
        · exact ih _ (by simp [onlineStepBuggy, hm]) (by simp [onlineStepBuggy, hm, hs, expDiff, sumMul, sumAdd])
  exact hgen rest (onlineStepBuggy {} true) rfl rfl

theorem runOnline_fixed_invariant (tiles : List TileHasFinite) (st : OnlineState)
    (hinv : (st.m = .negInf ∧ st.s = .zero) ∨ (st.m = .finite ∧ st.s = .pos)) :
    let st' := runOnline onlineStepFixed st tiles
    (tiles.any id = true → st'.m = .finite ∧ st'.s = .pos) ∧
    (st.m = .finite → st'.m = .finite ∧ st'.s = .pos) := by
  induction tiles generalizing st with
  | nil =>
      rcases hinv with ⟨hm, hs⟩ | ⟨hm, hs⟩
      · simp [runOnline, hm]
      · simp [runOnline, hm, hs]
  | cons b bs ih =>
      simp only [runOnline, List.foldl_cons, List.any_cons, Bool.or_eq_true, id_eq]
      rcases hinv with ⟨hm, hs⟩ | ⟨hm, hs⟩
      · cases b
        · have hstep : onlineStepFixed st false = { m := .negInf, s := .zero } := by
            simp [onlineStepFixed, hm, hs, expDiff, sumMul, sumAdd]
          rw [hstep]
          have hrec := ih { m := .negInf, s := .zero } (Or.inl ⟨rfl, rfl⟩)
          refine ⟨fun h => ?_, fun h => by simp [hm] at h⟩
          rcases h with hfalse | hany
          · cases hfalse
          · exact hrec.1 hany
        · have hstep : onlineStepFixed st true = { m := .finite, s := .pos } := by
            simp [onlineStepFixed, hm, hs, expDiff, sumMul, sumAdd]
          rw [hstep]
          have hrec := ih { m := .finite, s := .pos } (Or.inr ⟨rfl, rfl⟩)
          exact ⟨fun _ => hrec.2 rfl, fun _ => hrec.2 rfl⟩
      · cases b
        · have hstep : onlineStepFixed st false = { m := .finite, s := .pos } := by
            simp [onlineStepFixed, hm, hs, expDiff, sumMul, sumAdd]
          rw [hstep]
          have hrec := ih { m := .finite, s := .pos } (Or.inr ⟨rfl, rfl⟩)
          exact ⟨fun _ => hrec.2 rfl, fun _ => hrec.2 rfl⟩
        · have hstep : onlineStepFixed st true = { m := .finite, s := .pos } := by
            simp [onlineStepFixed, hm, hs, expDiff, sumMul, sumAdd]
          rw [hstep]
          have hrec := ih { m := .finite, s := .pos } (Or.inr ⟨rfl, rfl⟩)
          exact ⟨fun _ => hrec.2 rfl, fun _ => hrec.2 rfl⟩

theorem runOnline_fixed_all_neginf (tiles : List TileHasFinite) (st : OnlineState)
    (h0 : st.m = .negInf ∧ st.s = .zero) (hall : ∀ b ∈ tiles, b = false) :
    runOnline onlineStepFixed st tiles = { m := .negInf, s := .zero } := by
  induction tiles generalizing st with
  | nil =>
      rcases st with ⟨m, s⟩
      simp only at h0
      simp [runOnline, h0.1, h0.2]
  | cons b bs ih =>
      have hb : b = false := hall b (List.Mem.head _)
      subst hb
      have hstep : onlineStepFixed st false = { m := .negInf, s := .zero } := by
        simp [onlineStepFixed, h0.1, h0.2, expDiff, sumMul, sumAdd]
      simp only [runOnline, List.foldl_cons, hstep]
      exact ih { m := .negInf, s := .zero } ⟨rfl, rfl⟩ (fun x hx => hall x (List.Mem.tail _ hx))

/-! ### 4. Masked Load Padding Taint, Row Reductions & Indexed Load Bounds -/

/-- Value with explicit uninitialized/undefined UB lane state (Triton `padding_option=""`). -/
inductive LaneVal where
  | val (v : Int)
  | undef
  deriving DecidableEq, Repr

def laneMul (a b : LaneVal) : LaneVal :=
  match a, b with
  | .val x, .val y => .val (x * y)
  | _, _ => .undef

def laneAdd (a b : LaneVal) : LaneVal :=
  match a, b with
  | .val x, .val y => .val (x + y)
  | _, _ => .undef

/-- Row reduction `tl.sum` over `bn` lanes with UB taint tracking. -/
def sumRow (f : Nat → LaneVal) : Nat → LaneVal
  | 0 => .val 0
  | n + 1 => laneAdd (sumRow f n) (f n)

/-- Pure mathematical row sum over valid indices `0 .. n-1`. -/
def sumSpec (a : Nat → Int) : Nat → Int
  | 0 => 0
  | n + 1 => sumSpec a n + a n

theorem sumRow_zero_padded (a : Nat → Int) (n bn : Nat) (hnb : n ≤ bn) :
    sumRow (fun j => if j < n then .val (a j) else .val 0) bn = .val (sumSpec a n) := by
  induction bn with
  | zero =>
      have hn0 : n = 0 := by omega
      subst hn0; rfl
  | succ m ih =>
      by_cases hnm : n ≤ m
      · simp only [sumRow]
        rw [ih hnm]
        have hnot : ¬ (m < n) := by omega
        simp [hnot, laneAdd]
      · have hneq : n = m + 1 := by omega
        subst hneq
        simp only [sumRow]
        have hprefix :
            ∀ r, r ≤ m →
              sumRow (fun j => if j < m + 1 then .val (a j) else .val 0) r = .val (sumSpec a r) := by
          intro r hr
          induction r with
          | zero => rfl
          | succ t iht =>
              simp only [sumRow, iht (by omega)]
              have ht : t < m + 1 := by omega
              simp [ht, laneAdd, sumSpec]
        rw [hprefix m (Nat.le_refl m)]
        simp [laneAdd, sumSpec]

theorem sumRow_undef_poisons (a : Nat → Int) (n bn : Nat) (hlt : n < bn) :
    sumRow (fun j => if j < n then .val (a j) else .undef) bn = .undef := by
  induction bn with
  | zero => omega
  | succ m ih =>
      simp only [sumRow]
      by_cases hnm : n < m
      · rw [ih hnm]
        cases (if m < n then LaneVal.val (a m) else LaneVal.undef) <;> rfl
      · have hnot : ¬ (m < n) := by omega
        simp only [hnot, ↓reduceIte]
        cases sumRow (fun j => if j < n then .val (a j) else .undef) m <;> rfl

/-- Inner product of length `bk` where lanes `j < k` are valid and `j ≥ k` are padded. -/
def dotRow (f g : Nat → LaneVal) : Nat → LaneVal
  | 0 => .val 0
  | n + 1 => laneAdd (dotRow f g n) (laneMul (f n) (g n))

/-- Pure mathematical inner product over valid indices `0 .. k-1`. -/
def dotSpec (a b : Nat → Int) : Nat → Int
  | 0 => 0
  | n + 1 => dotSpec a b n + a n * b n

theorem dotRow_zero_padded (a b : Nat → Int) (k bk : Nat) (hkb : k ≤ bk) :
    dotRow (fun j => if j < k then .val (a j) else .val 0)
           (fun j => if j < k then .val (b j) else .val 0) bk =
    .val (dotSpec a b k) := by
  induction bk with
  | zero =>
      have hk0 : k = 0 := by omega
      subst hk0; rfl
  | succ m ih =>
      by_cases hkm : k ≤ m
      · simp only [dotRow]
        rw [ih hkm]
        have hnot : ¬ (m < k) := by omega
        simp [hnot, laneMul, laneAdd]
      · have hkeq : k = m + 1 := by omega
        subst hkeq
        simp only [dotRow]
        have hprefix :
            ∀ n, n ≤ m →
              dotRow (fun j => if j < m + 1 then .val (a j) else .val 0)
                     (fun j => if j < m + 1 then .val (b j) else .val 0) n =
              .val (dotSpec a b n) := by
          intro n hn
          induction n with
          | zero => rfl
          | succ r ihr =>
              simp only [dotRow, ihr (by omega)]
              have hr : r < m + 1 := by omega
              simp [hr, laneMul, laneAdd, dotSpec]
        rw [hprefix m (Nat.le_refl m)]
        simp [laneMul, laneAdd, dotSpec]

theorem dotRow_undef_poisons (a b : Nat → Int) (k bk : Nat) (hlt : k < bk) :
    dotRow (fun j => if j < k then .val (a j) else .undef)
           (fun j => if j < k then .val (b j) else .undef) bk = .undef := by
  induction bk with
  | zero => omega
  | succ m ih =>
      simp only [dotRow]
      by_cases hkm : k < m
      · rw [ih hkm]
        cases (if m < k then LaneVal.val (a m) else LaneVal.undef) <;>
        cases (if m < k then LaneVal.val (b m) else LaneVal.undef) <;>
        rfl
      · have hnot : ¬ (m < k) := by omega
        simp only [hnot, ↓reduceIte, laneMul]
        cases dotRow (fun j => if j < k then .val (a j) else .undef)
                     (fun j => if j < k then .val (b j) else .undef) m <;> rfl

/-- Memory-access safety predicate for an indexed load `ptr[idx]` gated by `mask`. -/
def indexedLoadSafe (bufLen : Nat) (idx : Int) (mask : Bool) : Prop :=
  mask = true → 0 ≤ idx ∧ idx < bufLen

end AscendLean.TritonModel
