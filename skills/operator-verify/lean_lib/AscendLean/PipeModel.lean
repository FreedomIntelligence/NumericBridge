/-
  AscendLean.PipeModel
  Formal execution & synchronization model for Ascend DaVinci AI Core pipelines
  (PIPE_MTE2, PIPE_V, PIPE_MTE3, PIPE_S) and FIFO HardEvent synchronization
  (SetFlag / WaitFlag) across all 12 directed cross-pipe HardEvents, targeting
  UB data-race freedom (RAW / WAR / WAW), deadlock freedom (no wait underflow),
  and event balance.
-/

namespace AscendLean.PipeModel

inductive Pipe where
  | MTE2
  | V
  | MTE3
  | S
  deriving DecidableEq, Repr

inductive HardEvent where
  | MTE3_MTE2
  | MTE2_MTE3
  | MTE2_V
  | V_MTE2
  | V_MTE3
  | MTE3_V
  | S_MTE2
  | MTE2_S
  | S_V
  | V_S
  | S_MTE3
  | MTE3_S
  deriving DecidableEq, Repr

def HardEvent.src : HardEvent → Pipe
  | .MTE3_MTE2 => .MTE3
  | .MTE2_MTE3 => .MTE2
  | .MTE2_V    => .MTE2
  | .V_MTE2    => .V
  | .V_MTE3    => .V
  | .MTE3_V    => .MTE3
  | .S_MTE2    => .S
  | .MTE2_S    => .MTE2
  | .S_V       => .S
  | .V_S       => .V
  | .S_MTE3    => .S
  | .MTE3_S    => .MTE3

def HardEvent.dst : HardEvent → Pipe
  | .MTE3_MTE2 => .MTE2
  | .MTE2_MTE3 => .MTE3
  | .MTE2_V    => .V
  | .V_MTE2    => .MTE2
  | .V_MTE3    => .MTE3
  | .MTE3_V    => .V
  | .S_MTE2    => .MTE2
  | .MTE2_S    => .S
  | .S_V       => .V
  | .V_S       => .S
  | .S_MTE3    => .MTE3
  | .MTE3_S    => .S

abbrev BufId := String

inductive Op where
  | write (pipe : Pipe) (buf : BufId) (tag : String)
  | read  (pipe : Pipe) (buf : BufId) (tag : String)
  | setFlag (ev : HardEvent)
  | waitFlag (ev : HardEvent)
  | pipeBarrierAll
  deriving DecidableEq, Repr

/-- Per-pipe last access timestamp on `targetBuf`. -/
structure PipeTimes where
  mte2 : Nat := 0
  v    : Nat := 0
  mte3 : Nat := 0
  s    : Nat := 0
  deriving DecidableEq, Repr

def PipeTimes.get (t : PipeTimes) : Pipe → Nat
  | .MTE2 => t.mte2
  | .V    => t.v
  | .MTE3 => t.mte3
  | .S    => t.s

def PipeTimes.set (t : PipeTimes) (p : Pipe) (val : Nat) : PipeTimes :=
  match p with
  | .MTE2 => { t with mte2 := val }
  | .V    => { t with v := val }
  | .MTE3 => { t with mte3 := val }
  | .S    => { t with s := val }

/-- Latest synchronized timestamp from `src` pipe visible to `dst` pipe via each HardEvent. -/
structure SyncWatermark where
  mte3_mte2 : Nat := 0
  mte2_mte3 : Nat := 0
  mte2_v    : Nat := 0
  v_mte2    : Nat := 0
  v_mte3    : Nat := 0
  mte3_v    : Nat := 0
  s_mte2    : Nat := 0
  mte2_s    : Nat := 0
  s_v       : Nat := 0
  v_s       : Nat := 0
  s_mte3    : Nat := 0
  mte3_s    : Nat := 0
  allBarrier : Nat := 0
  deriving DecidableEq, Repr

def SyncWatermark.knownSafe (w : SyncWatermark) (src dst : Pipe) : Nat :=
  let direct :=
    match src, dst with
    | .MTE3, .MTE2 => w.mte3_mte2
    | .MTE2, .MTE3 => w.mte2_mte3
    | .MTE2, .V    => w.mte2_v
    | .V,    .MTE2 => w.v_mte2
    | .V,    .MTE3 => w.v_mte3
    | .MTE3, .V    => w.mte3_v
    | .S,    .MTE2 => w.s_mte2
    | .MTE2, .S    => w.mte2_s
    | .S,    .V    => w.s_v
    | .V,    .S    => w.v_s
    | .S,    .MTE3 => w.s_mte3
    | .MTE3, .S    => w.mte3_s
    | _,     _     => 0
  max direct w.allBarrier

def SyncWatermark.advance (w : SyncWatermark) (ev : HardEvent) (val : Nat) : SyncWatermark :=
  match ev with
  | .MTE3_MTE2 => { w with mte3_mte2 := max w.mte3_mte2 val }
  | .MTE2_MTE3 => { w with mte2_mte3 := max w.mte2_mte3 val }
  | .MTE2_V    => { w with mte2_v    := max w.mte2_v val }
  | .V_MTE2    => { w with v_mte2    := max w.v_mte2 val }
  | .V_MTE3    => { w with v_mte3    := max w.v_mte3 val }
  | .MTE3_V    => { w with mte3_v    := max w.mte3_v val }
  | .S_MTE2    => { w with s_mte2    := max w.s_mte2 val }
  | .MTE2_S    => { w with mte2_s    := max w.mte2_s val }
  | .S_V       => { w with s_v       := max w.s_v val }
  | .V_S       => { w with v_s       := max w.v_s val }
  | .S_MTE3    => { w with s_mte3    := max w.s_mte3 val }
  | .MTE3_S    => { w with mte3_s    := max w.mte3_s val }

/-- Per-HardEvent FIFO signal queues (models hardware SetFlag/WaitFlag token counters). -/
structure EventQueues where
  mte3_mte2 : List Nat := []
  mte2_mte3 : List Nat := []
  mte2_v    : List Nat := []
  v_mte2    : List Nat := []
  v_mte3    : List Nat := []
  mte3_v    : List Nat := []
  s_mte2    : List Nat := []
  mte2_s    : List Nat := []
  s_v       : List Nat := []
  v_s       : List Nat := []
  s_mte3    : List Nat := []
  mte3_s    : List Nat := []
  deriving DecidableEq, Repr

def EventQueues.push (q : EventQueues) (ev : HardEvent) (val : Nat) : EventQueues :=
  match ev with
  | .MTE3_MTE2 => { q with mte3_mte2 := q.mte3_mte2 ++ [val] }
  | .MTE2_MTE3 => { q with mte2_mte3 := q.mte2_mte3 ++ [val] }
  | .MTE2_V    => { q with mte2_v    := q.mte2_v ++ [val] }
  | .V_MTE2    => { q with v_mte2    := q.v_mte2 ++ [val] }
  | .V_MTE3    => { q with v_mte3    := q.v_mte3 ++ [val] }
  | .MTE3_V    => { q with mte3_v    := q.mte3_v ++ [val] }
  | .S_MTE2    => { q with s_mte2    := q.s_mte2 ++ [val] }
  | .MTE2_S    => { q with mte2_s    := q.mte2_s ++ [val] }
  | .S_V       => { q with s_v       := q.s_v ++ [val] }
  | .V_S       => { q with v_s       := q.v_s ++ [val] }
  | .S_MTE3    => { q with s_mte3    := q.s_mte3 ++ [val] }
  | .MTE3_S    => { q with mte3_s    := q.mte3_s ++ [val] }

def EventQueues.pop (q : EventQueues) (ev : HardEvent) : Option (Nat × EventQueues) :=
  match ev with
  | .MTE3_MTE2 => match q.mte3_mte2 with | [] => none | e :: r => some (e, { q with mte3_mte2 := r })
  | .MTE2_MTE3 => match q.mte2_mte3 with | [] => none | e :: r => some (e, { q with mte2_mte3 := r })
  | .MTE2_V    => match q.mte2_v    with | [] => none | e :: r => some (e, { q with mte2_v := r })
  | .V_MTE2    => match q.v_mte2    with | [] => none | e :: r => some (e, { q with v_mte2 := r })
  | .V_MTE3    => match q.v_mte3    with | [] => none | e :: r => some (e, { q with v_mte3 := r })
  | .MTE3_V    => match q.mte3_v    with | [] => none | e :: r => some (e, { q with mte3_v := r })
  | .S_MTE2    => match q.s_mte2    with | [] => none | e :: r => some (e, { q with s_mte2 := r })
  | .MTE2_S    => match q.mte2_s    with | [] => none | e :: r => some (e, { q with mte2_s := r })
  | .S_V       => match q.s_v       with | [] => none | e :: r => some (e, { q with s_v := r })
  | .V_S       => match q.v_s       with | [] => none | e :: r => some (e, { q with v_s := r })
  | .S_MTE3    => match q.s_mte3    with | [] => none | e :: r => some (e, { q with s_mte3 := r })
  | .MTE3_S    => match q.mte3_s    with | [] => none | e :: r => some (e, { q with mte3_s := r })

def EventQueues.allEmpty (q : EventQueues) : Bool :=
  q.mte3_mte2.isEmpty && q.mte2_mte3.isEmpty && q.mte2_v.isEmpty && q.v_mte2.isEmpty &&
  q.v_mte3.isEmpty && q.mte3_v.isEmpty && q.s_mte2.isEmpty && q.mte2_s.isEmpty &&
  q.s_v.isEmpty && q.v_s.isEmpty && q.s_mte3.isEmpty && q.mte3_s.isEmpty

/--
  Hardware synchronization state tracking all 4 pipes and all 12 directed `HardEvent`
  FIFO queues on a target UB buffer `targetBuf`. Detects cross-pipe RAW, WAR, and WAW
  hazards, wait underflow (deadlock), and leaked SetFlag tokens.
-/
structure SyncState where
  step : Nat := 0
  lastRead : PipeTimes := {}
  lastWrite : PipeTimes := {}
  synced : SyncWatermark := {}
  queues : EventQueues := {}
  raceDetected : Bool := false
  underflow : Bool := false
  deriving DecidableEq, Repr

private def allPipes : List Pipe := [.MTE2, .V, .MTE3, .S]

private def hasRawHazard (st : SyncState) (dst : Pipe) : Bool :=
  allPipes.any fun src =>
    src != dst && st.synced.knownSafe src dst < st.lastWrite.get src

private def hasWarOrWawHazard (st : SyncState) (dst : Pipe) : Bool :=
  allPipes.any fun src =>
    src != dst &&
    (st.synced.knownSafe src dst < st.lastRead.get src ||
     st.synced.knownSafe src dst < st.lastWrite.get src)

def stepOp (targetBuf : BufId) (st : SyncState) (op : Op) : SyncState :=
  let nextStep := st.step + 1
  match op with
  | Op.read p b _ =>
      if b == targetBuf then
        let raced := st.raceDetected || hasRawHazard st p
        { st with
          step := nextStep,
          lastRead := st.lastRead.set p nextStep,
          raceDetected := raced }
      else
        { st with step := nextStep }
  | Op.write p b _ =>
      if b == targetBuf then
        let raced := st.raceDetected || hasWarOrWawHazard st p
        { st with
          step := nextStep,
          lastWrite := st.lastWrite.set p nextStep,
          raceDetected := raced }
      else
        { st with step := nextStep }
  | Op.setFlag ev =>
      let src := ev.src
      let ts := max (st.lastRead.get src) (st.lastWrite.get src)
      { st with step := nextStep, queues := st.queues.push ev ts }
  | Op.waitFlag ev =>
      match st.queues.pop ev with
      | none => { st with step := nextStep, underflow := true }
      | some (e, q') =>
          { st with
            step := nextStep,
            synced := st.synced.advance ev e,
            queues := q' }
  | Op.pipeBarrierAll =>
      { st with step := nextStep, synced := { st.synced with allBarrier := nextStep } }

def runOps (targetBuf : BufId) (st : SyncState) (ops : List Op) : SyncState :=
  ops.foldl (stepOp targetBuf) st

theorem runOps_nil (targetBuf : BufId) (st : SyncState) :
    runOps targetBuf st [] = st := rfl

theorem runOps_cons (targetBuf : BufId) (st : SyncState) (op : Op) (ops : List Op) :
    runOps targetBuf st (op :: ops) = runOps targetBuf (stepOp targetBuf st op) ops := rfl

theorem runOps_append (targetBuf : BufId) (st : SyncState) (xs ys : List Op) :
    runOps targetBuf st (xs ++ ys) = runOps targetBuf (runOps targetBuf st xs) ys := by
  simp [runOps, List.foldl_append]

/-- Kernel is race-free, deadlock-free (no event underflow), and leaves no leaked tokens. -/
abbrev VerifiedSafe (st : SyncState) : Prop :=
  st.raceDetected = false ∧ st.underflow = false ∧ st.queues.allEmpty = true

/-- Sanity theorem: unsynchronized MTE2 write followed by V read on the same UB buffer detects a RAW race. -/
theorem mte2_v_unsync_race_detected :
    (runOps "ub" {} [Op.write .MTE2 "ub" "load", Op.read .V "ub" "compute"]).raceDetected = true := by
  decide

/-- Sanity theorem: properly synchronized MTE2 -> V sequence is verified safe. -/
theorem mte2_v_synced_safe :
    VerifiedSafe (runOps "ub" {} [
      Op.write .MTE2 "ub" "load",
      Op.setFlag .MTE2_V,
      Op.waitFlag .MTE2_V,
      Op.read .V "ub" "compute"
    ]) := by
  decide

/-- Sanity theorem: properly synchronized V -> S sequence is verified safe. -/
theorem v_s_synced_safe :
    VerifiedSafe (runOps "ub" {} [
      Op.write .V "ub" "compute",
      Op.setFlag .V_S,
      Op.waitFlag .V_S,
      Op.read .S "ub" "scalar_read"
    ]) := by
  decide

end AscendLean.PipeModel
