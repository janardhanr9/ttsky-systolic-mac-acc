<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

This project implements a **1D Systolic Array with 8 Processing Elements (PEs)** for multiply-accumulate (MAC) operations, optimized for the Tiny Tapeout Sky130 process. The design follows a weight-stationary architecture with deterministic timing and no handshaking.

### Architecture Overview

The systolic array consists of:

- **8 Processing Elements (PEs)** arranged in a 1D chain
- **Systolic Controller** managing the FSM and control signals
- **4-bit signed integer arithmetic** for inputs (weights, biases, activations)
- **8-bit signed accumulators** with saturation logic to prevent overflow

### Processing Element (PE) Design

Each PE contains:
- A 4-bit weight register (stationary, loaded once)
- An 8-bit accumulator with saturation logic for summing results
- Multiply-accumulate logic: `accumulator = saturate(accumulator + (data_in × weight))`
- Pass-through data path for chaining PEs
- Saturation range: -128 to 127 (8-bit signed)

### Systolic Data Flow

1. **Weight Loading**: Weights are loaded sequentially into each PE (W0, W1, W2, W3, W4, W5, W6, W7)
2. **Bias Loading**: Biases are loaded sequentially into each PE's accumulator (B0, B1, B2, B3, B4, B5, B6, B7)
3. **Compute Phase**: Activation data flows through the chain:
   - PE0 receives data on cycle 0, computes for 8 cycles
   - PE1 receives data on cycle 1, computes for 8 cycles
   - PE2 receives data on cycle 2, computes for 8 cycles
   - PE3 receives data on cycle 3, computes for 8 cycles
   - PE4 receives data on cycle 4, computes for 8 cycles
   - PE5 receives data on cycle 5, computes for 8 cycles
   - PE6 receives data on cycle 6, computes for 8 cycles
   - PE7 receives data on cycle 7, computes for 8 cycles
4. **Drain Phase**: Results are read out sequentially from each PE

### FSM States

The controller implements a 5-state FSM:
- **IDLE**: Initial state, transitions immediately to LOAD_W
- **LOAD_W**: Load weights (8 cycles, one per PE)
- **LOAD_B**: Load biases (8 cycles, one per PE)
- **COMPUTE**: MAC operations (15 cycles to fill pipeline and compute)
- **DRAIN**: Output accumulated results (8 cycles, one per PE)

### I/O Interface

- **ui_in[3:0]**: 4-bit data input (weights during LOAD_W, biases during LOAD_B, activations during COMPUTE)
- **ui_in[7:4]**: Unused
- **uo_out[7:0]**: 8-bit signed result during DRAIN
- **uio_out[7:0]**: Unused (all zeros)
- **uio_oe[7:0]**: Bidirectional enable (always output = 0xFF)

## How to test

### Automated Testing

The design includes a cocotb test suite with 12 test cases. Every functional
test asserts against a golden reference model (`test/reference_model.py`)
written from this datasheet rather than from the RTL, so the model cannot
silently encode implementation bugs.

1. **Reference Model Self-Check**: Confirms the model reproduces the worked example below
2. **FSM State Durations**: Asserts each state lasts exactly 1/8/8/15/8 cycles
3. **Cycle Counter**: Asserts `cycle_count` runs 0..N-1 and restarts on every state entry
4. **Reset Coverage**: Asserts reset clears every PE register, including the pass-through pipeline
5. **Weight and Bias Loading**: Confirms values land in the right PE, including negatives
6. **Datasheet Example**: Drives the worked example below end to end
7. **Negative Weights**: Asserts signed products, not zero-extended ones
8. **Negative Activations**: Signed activations against positive weights
9. **Saturation**: Asserts both the +127 and -128 rails clamp rather than wrap
10. **Drain Ordering**: Asserts DRAIN emits PE0..PE7 on eight consecutive cycles
11. **Randomized**: Eight random weight/bias/activation vectors vs. the model
12. **Back to Back**: Two consecutive passes, checking no state carries over

Run the tests:
```bash
cd test
make clean
make
```

View waveforms:
```bash
gtkwave tb.fst tb.gtkw
```

### Manual Testing Procedure

1. **Reset**: Assert `rst_n = 0` for at least 5 clock cycles, then release
2. **Load Weights**: The system automatically enters LOAD_W state. Apply 8 weight values on `ui_in[3:0]`, one per clock cycle (range: -8 to 7)
3. **Load Biases**: System transitions to LOAD_B. Apply 8 bias values on `ui_in[3:0]`, one per clock cycle (range: -8 to 7)
4. **Compute**: System transitions to COMPUTE. Apply 15 activation values on `ui_in[3:0]` (first 8 are processed, remaining 7 are for pipeline flushing)
5. **Drain**: System transitions to DRAIN. Read results from `uo_out[7:0]` (8-bit signed values with saturation, range: -128 to 127):
   - Cycle 0: PE0 result (sum of 8 MAC operations, saturated)
   - Cycle 1: PE1 result
   - Cycle 2: PE2 result
   - Cycle 3: PE3 result
   - Cycle 4: PE4 result
   - Cycle 5: PE5 result
   - Cycle 6: PE6 result
   - Cycle 7: PE7 result

### Example Test Case

Load: W = [2, 3, 4, 5, 6, 7, 1, 2], B = [0, 0, 0, 0, 0, 0, 0, 0]
Compute: Data = [7, 7, 7, 7, 7, 7, 7, 7, 0, 0, 0, 0, 0, 0, 0] (max 4-bit signed value)

Expected results (with saturation):
- PE0: 7×2 × 8 = 112
- PE1: 7×3 × 8 = 168 → **saturated to 127**
- PE2: 7×4 × 8 = 224 → **saturated to 127**
- PE3: 7×5 × 8 = 280 → **saturated to 127**
- PE4: 7×6 × 8 = 336 → **saturated to 127**
- PE5: 7×7 × 8 = 392 → **saturated to 127**
- PE6: 7×1 × 8 = 56
- PE7: 7×2 × 8 = 112

Note: Results exceeding ±127 will be saturated to the 8-bit signed range.

### Timing Constraints

- Clock frequency: 50 MHz (20ns period)
- Total cycle count per operation: 40 cycles (1 IDLE + 8 LOAD_W + 8 LOAD_B + 15 COMPUTE + 8 DRAIN)
- The FSM wraps straight back to IDLE, so passes can be run back to back
- Deterministic latency: No stalls or variable timing

## External hardware

No external hardware is required. The design is fully self-contained and can be tested using:
- Clock source (50 MHz recommended)
- 4-bit data input bus (ui_in[3:0])
- 8-bit result output bus (uo_out[7:0])
