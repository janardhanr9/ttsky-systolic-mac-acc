# SPDX-License-Identifier: Apache-2.0
"""Golden reference model for the 8-PE 1D systolic MAC array.

This model is written *from the datasheet* (docs/info.md), not from the RTL.
That is deliberate: a model derived from the implementation can only ever
confirm that the implementation does what it does. Everything here traces back
to a specific claim in the datasheet, noted inline.

Datasheet claims encoded below:
  * 8 PEs in a 1D chain, weight-stationary.
  * 4-bit signed inputs (weights, biases, activations), range -8..7.
  * 8-bit signed accumulator, saturating to -128..127.
  * Bias is sign-extended into the accumulator before compute.
  * MAC rule: accumulator = saturate(accumulator + data_in * weight).
  * COMPUTE runs 15 cycles; PE i first receives data on cycle i and
    accumulates for 8 cycles.
  * DRAIN emits PE0..PE7 on 8 consecutive cycles.
"""

N_PE = 8
COMPUTE_CYCLES = 15
ACC_MIN = -128
ACC_MAX = 127

# Cycles spent in each FSM state, per the datasheet's "FSM States" section.
STATE_CYCLES = {
    "IDLE": 1,
    "LOAD_W": 8,
    "LOAD_B": 8,
    "COMPUTE": 15,
    "DRAIN": 8,
}
STATE_ORDER = ["IDLE", "LOAD_W", "LOAD_B", "COMPUTE", "DRAIN"]


def saturate(value):
    """Clamp to the 8-bit signed accumulator range."""
    return max(ACC_MIN, min(ACC_MAX, value))


def check_s4(value, what):
    if not -8 <= value <= 7:
        raise ValueError(f"{what}={value} outside 4-bit signed range -8..7")
    return value


def to_u4(value):
    """Signed 4-bit value -> raw bits to drive onto ui_in[3:0]."""
    return check_s4(value, "input") & 0xF


def from_u8(raw):
    """Raw uo_out bits -> signed 8-bit Python int."""
    raw &= 0xFF
    return raw - 256 if raw & 0x80 else raw


def systolic_reference(weights, biases, activations):
    """Return the eight expected accumulator values at the end of COMPUTE.

    weights, biases  -- 8 signed 4-bit ints each (PE0..PE7)
    activations      -- 15 signed 4-bit ints, one per COMPUTE cycle

    Written cycle-accurately rather than as a closed form so the timing
    assumption (PE i sees the activation launched i cycles earlier) is
    visible and testable rather than baked into an algebraic shortcut.
    """
    if len(weights) != N_PE:
        raise ValueError(f"expected {N_PE} weights, got {len(weights)}")
    if len(biases) != N_PE:
        raise ValueError(f"expected {N_PE} biases, got {len(biases)}")
    if len(activations) != COMPUTE_CYCLES:
        raise ValueError(
            f"expected {COMPUTE_CYCLES} activations, got {len(activations)}"
        )

    for i, w in enumerate(weights):
        check_s4(w, f"weight[{i}]")
    for i, b in enumerate(biases):
        check_s4(b, f"bias[{i}]")
    for c, a in enumerate(activations):
        check_s4(a, f"activation[{c}]")

    # Bias is loaded into the accumulator sign-extended; a 4-bit signed value
    # always fits in 8 bits, so no saturation can occur here.
    acc = list(biases)

    for cycle in range(COMPUTE_CYCLES):
        for i in range(N_PE):
            # PE i is fed for 8 cycles starting at cycle i. The datum reaching
            # it on cycle `cycle` was launched onto the chain at cycle-i and
            # has been passed through i registers to get here.
            if i <= cycle < i + N_PE:
                acc[i] = saturate(acc[i] + activations[cycle - i] * weights[i])

    return acc


def explain(weights, biases, activations):
    """Per-PE step-by-step trace. Used to make assertion failures readable."""
    lines = []
    acc = list(biases)
    for i in range(N_PE):
        steps = [f"PE{i}: bias={biases[i]} w={weights[i]}"]
        for cycle in range(i, i + N_PE):
            a = activations[cycle - i]
            before = acc[i]
            acc[i] = saturate(acc[i] + a * weights[i])
            steps.append(
                f"  cyc{cycle:2d}: {before} + {a}*{weights[i]} -> {acc[i]}"
            )
        lines.append("\n".join(steps))
    return "\n".join(lines)
