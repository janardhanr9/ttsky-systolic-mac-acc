# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0
"""Cocotb suite for the 8-PE systolic MAC array.

Every functional test compares the DUT against `reference_model.py`, which is
written from the datasheet rather than from the RTL. Tests assert; they do not
merely log.

Clocking convention: all stimulus is driven on the falling edge and all
sampling is done on the falling edge, so nothing races the rising edge that
the DUT clocks on. `reset_dut` leaves the DUT parked in the single IDLE cycle;
one further falling edge puts us in LOAD_W cycle 0.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

from reference_model import (
    COMPUTE_CYCLES,
    N_PE,
    STATE_CYCLES,
    STATE_ORDER,
    explain,
    from_u8,
    systolic_reference,
    to_u4,
)

# Encoding of the `state_t` typedef in systolic_controller.sv.
STATE_NAMES = {0: "IDLE", 1: "LOAD_W", 2: "LOAD_B", 3: "COMPUTE", 4: "DRAIN"}


def ctrl(dut):
    return dut.user_project.controller


def pe(dut, i):
    return ctrl(dut).sa_inst.pe_chain[i].pe_inst


def resolvable(handle):
    """True if the signal currently holds no X/Z bits."""
    return "x" not in str(handle.value).lower() and "z" not in str(handle.value).lower()


def read_signed8(handle):
    """Signed value of an 8-bit signal, or None if it holds X/Z."""
    if not resolvable(handle):
        return None
    return from_u8(int(handle.value))


def read_signed4(handle):
    if not resolvable(handle):
        return None
    raw = int(handle.value) & 0xF
    return raw - 16 if raw & 0x8 else raw


def fmt(values):
    return "[" + ", ".join("X" if v is None else str(v) for v in values) + "]"


async def start_clock(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="us").start())


async def reset_dut(dut):
    """Reset and leave the DUT parked in the IDLE cycle."""
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    # We are now inside the IDLE cycle; the next rising edge enters LOAD_W.


async def run_pass(dut, weights, biases, activations):
    """Drive one complete LOAD_W/LOAD_B/COMPUTE/DRAIN pass per the datasheet.

    Returns the eight values observed on uo_out during DRAIN (None for any
    cycle where the output held X/Z). Leaves the DUT positioned at LOAD_W
    cycle 0 of the following pass, so passes can be chained.
    """
    await FallingEdge(dut.clk)  # IDLE -> LOAD_W cycle 0

    for w in weights:
        dut.ui_in.value = to_u4(w)
        await FallingEdge(dut.clk)

    for b in biases:
        dut.ui_in.value = to_u4(b)
        await FallingEdge(dut.clk)

    for a in activations:
        dut.ui_in.value = to_u4(a)
        await FallingEdge(dut.clk)

    # Now in DRAIN cycle 0.
    results = []
    for _ in range(N_PE):
        results.append(read_signed8(dut.uo_out))
        await FallingEdge(dut.clk)

    return results


def assert_matches(dut, weights, biases, activations, actual, label):
    expected = systolic_reference(weights, biases, activations)
    if actual != expected:
        mismatches = [
            f"  PE{i}: expected {e}, got {'X/Z' if a is None else a}"
            for i, (e, a) in enumerate(zip(expected, actual))
            if e != a
        ]
        raise AssertionError(
            f"{label}: drained accumulators do not match the reference model.\n"
            f"  weights     = {weights}\n"
            f"  biases      = {biases}\n"
            f"  activations = {activations}\n"
            f"  expected    = {expected}\n"
            f"  actual      = {fmt(actual)}\n"
            + "\n".join(mismatches)
            + "\n\nReference trace:\n"
            + explain(weights, biases, activations)
        )
    dut._log.info(f"{label}: {fmt(actual)} matches reference model")


# --------------------------------------------------------------------------
# Model self-check
# --------------------------------------------------------------------------


@cocotb.test()
async def test_reference_model_matches_datasheet(dut):
    """The model must reproduce the worked example published in docs/info.md."""
    weights = [2, 3, 4, 5, 6, 7, 1, 2]
    biases = [0] * N_PE
    activations = [7] * 8 + [0] * 7
    datasheet = [112, 127, 127, 127, 127, 127, 56, 112]

    got = systolic_reference(weights, biases, activations)
    assert got == datasheet, (
        "reference model disagrees with the datasheet's own worked example:\n"
        f"  datasheet = {datasheet}\n  model     = {got}"
    )


# --------------------------------------------------------------------------
# FSM / control-path tests
# --------------------------------------------------------------------------


@cocotb.test()
async def test_fsm_state_durations(dut):
    """Each state must last exactly as long as the datasheet says."""
    await start_clock(dut)
    await reset_dut(dut)

    total = sum(STATE_CYCLES.values())
    observed = []
    for _ in range(total + 1):  # one extra to see the wrap back to IDLE
        observed.append(STATE_NAMES.get(int(ctrl(dut).state.value), "??"))
        await FallingEdge(dut.clk)

    # Run-length encode the observed state sequence.
    runs = []
    for name in observed:
        if runs and runs[-1][0] == name:
            runs[-1][1] += 1
        else:
            runs.append([name, 1])

    expected = [[s, STATE_CYCLES[s]] for s in STATE_ORDER] + [["IDLE", 1]]
    assert runs == expected, (
        "FSM state durations are wrong.\n"
        f"  expected = {expected}\n"
        f"  observed = {runs}\n"
        f"  raw      = {observed}"
    )


@cocotb.test()
async def test_cycle_count_restarts_each_state(dut):
    """cycle_count must run 0..N-1 within each state, restarting on entry."""
    await start_clock(dut)
    await reset_dut(dut)

    seen = {}
    total = sum(STATE_CYCLES.values())
    for _ in range(total):
        state = STATE_NAMES.get(int(ctrl(dut).state.value), "??")
        seen.setdefault(state, []).append(int(ctrl(dut).cycle_count.value))
        await FallingEdge(dut.clk)

    problems = []
    for state in STATE_ORDER:
        want = list(range(STATE_CYCLES[state]))
        got = seen.get(state, [])
        if got != want:
            problems.append(f"  {state}: expected {want}, got {got}")

    assert not problems, "cycle_count is wrong per state:\n" + "\n".join(problems)


@cocotb.test()
async def test_reset_clears_all_pe_state(dut):
    """Reset must clear every PE register, including the pass-through pipeline.

    Checking "is it X after reset" would only bite on a cold simulation -- once
    earlier tests have clocked the design, an unreset register holds stale data
    rather than X. So push known non-zero data through the chain first, then
    reset, and require everything to come back to zero. That catches both the
    cold-start X and the stale-data case, regardless of test ordering.
    """
    await start_clock(dut)
    await reset_dut(dut)

    # Fill the chain with a known non-zero pattern.
    await FallingEdge(dut.clk)
    for _ in range(20):
        dut.ui_in.value = 0xF
        await FallingEdge(dut.clk)

    # Now reset and check the whole PE state is back to a known zero.
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 3)
    await FallingEdge(dut.clk)

    problems = []
    for i in range(N_PE):
        for name in ("accumulator", "weight", "pass_through_data"):
            handle = getattr(pe(dut, i), name)
            if not resolvable(handle):
                problems.append(f"  PE{i}.{name} = {handle.value} (X/Z after reset)")
            elif int(handle.value) != 0:
                problems.append(
                    f"  PE{i}.{name} = {int(handle.value)} "
                    "(not cleared -- missing from the reset branch?)"
                )

    assert not problems, (
        "reset did not clear all PE state:\n" + "\n".join(problems)
    )


@cocotb.test()
async def test_weight_and_bias_loading(dut):
    """Weights and biases must land in the right PE, including negatives."""
    await start_clock(dut)
    await reset_dut(dut)
    await FallingEdge(dut.clk)  # LOAD_W cycle 0

    weights = [1, -2, 3, -4, 5, -6, 7, -8]
    for w in weights:
        dut.ui_in.value = to_u4(w)
        await FallingEdge(dut.clk)

    got_w = [read_signed4(pe(dut, i).weight) for i in range(N_PE)]
    assert got_w == weights, (
        f"weights mis-loaded: expected {weights}, got {fmt(got_w)}"
    )

    biases = [0, 7, -8, 3, -1, 6, -5, 2]
    for b in biases:
        dut.ui_in.value = to_u4(b)
        await FallingEdge(dut.clk)

    got_b = [read_signed8(pe(dut, i).accumulator) for i in range(N_PE)]
    assert got_b == biases, (
        "biases mis-loaded (or not sign-extended into the accumulator): "
        f"expected {biases}, got {fmt(got_b)}"
    )


# --------------------------------------------------------------------------
# Datapath tests -- all assert against the reference model
# --------------------------------------------------------------------------


@cocotb.test()
async def test_mac_datasheet_example(dut):
    """The datasheet's worked example, end to end through the DUT."""
    await start_clock(dut)
    await reset_dut(dut)

    weights = [2, 3, 4, 5, 6, 7, 1, 2]
    biases = [0] * N_PE
    activations = [7] * 8 + [0] * 7

    actual = await run_pass(dut, weights, biases, activations)
    assert_matches(dut, weights, biases, activations, actual, "datasheet example")


@cocotb.test()
async def test_mac_signed_negative_weights(dut):
    """Negative weights must produce negative products, not zero-extended ones."""
    await start_clock(dut)
    await reset_dut(dut)

    weights = [-1] * N_PE
    biases = [5] * N_PE
    activations = [5] * 8 + [0] * 7

    actual = await run_pass(dut, weights, biases, activations)
    assert_matches(dut, weights, biases, activations, actual, "negative weights")


@cocotb.test()
async def test_mac_signed_negative_activations(dut):
    """Negative activations, positive weights."""
    await start_clock(dut)
    await reset_dut(dut)

    weights = [1, 2, 3, 4, 5, 6, 7, 1]
    biases = [-8, -4, 0, 4, 7, 0, -2, 1]
    activations = [-3] * 8 + [0] * 7

    actual = await run_pass(dut, weights, biases, activations)
    assert_matches(dut, weights, biases, activations, actual, "negative activations")


@cocotb.test()
async def test_mac_saturation_both_rails(dut):
    """Positive and negative saturation must clamp, not wrap."""
    await start_clock(dut)
    await reset_dut(dut)

    # PE0..3 drive hard positive, PE4..7 drive hard negative.
    weights = [7, 7, 7, 7, -8, -8, -8, -8]
    biases = [7, 7, 7, 7, -8, -8, -8, -8]
    activations = [7] * 8 + [0] * 7

    actual = await run_pass(dut, weights, biases, activations)
    assert_matches(dut, weights, biases, activations, actual, "saturation")

    expected = systolic_reference(weights, biases, activations)
    assert expected[:4] == [127] * 4 and expected[4:] == [-128] * 4, (
        f"test vector no longer exercises both rails: {expected}"
    )


@cocotb.test()
async def test_drain_emits_all_eight_pes_in_order(dut):
    """DRAIN must present PE0..PE7 on eight consecutive cycles."""
    await start_clock(dut)
    await reset_dut(dut)

    weights = [1, 2, 3, -1, -2, -3, 4, -4]
    biases = [0, 1, 2, 3, -1, -2, -3, 0]
    activations = [1] * 8 + [0] * 7

    expected = systolic_reference(weights, biases, activations)
    assert len(set(expected)) == N_PE, (
        f"vector must give every PE a distinct value to prove ordering: {expected}"
    )

    actual = await run_pass(dut, weights, biases, activations)
    assert_matches(dut, weights, biases, activations, actual, "drain ordering")


@cocotb.test()
async def test_mac_randomized(dut):
    """Randomized vectors, checked against the model. Fixed seed for repeatability."""
    await start_clock(dut)
    await reset_dut(dut)

    rng = random.Random(0xC0FFEE)
    for trial in range(8):
        weights = [rng.randint(-8, 7) for _ in range(N_PE)]
        biases = [rng.randint(-8, 7) for _ in range(N_PE)]
        activations = [rng.randint(-8, 7) for _ in range(COMPUTE_CYCLES)]

        actual = await run_pass(dut, weights, biases, activations)
        assert_matches(dut, weights, biases, activations, actual, f"random trial {trial}")


@cocotb.test()
async def test_back_to_back_passes(dut):
    """Two full passes in a row must both be correct -- no state carried over."""
    await start_clock(dut)
    await reset_dut(dut)

    vectors = [
        ([2, 3, 4, 5, 6, 7, 1, 2], [0] * N_PE, [3] * 8 + [0] * 7),
        ([-1, 1, -2, 2, -3, 3, -4, 4], [1, -1, 2, -2, 3, -3, 4, -4], [2] * 8 + [0] * 7),
    ]

    for n, (weights, biases, activations) in enumerate(vectors):
        actual = await run_pass(dut, weights, biases, activations)
        assert_matches(dut, weights, biases, activations, actual, f"pass {n}")
