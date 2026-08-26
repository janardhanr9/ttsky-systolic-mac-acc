# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0

"""
Cocotb testbench for the 8-stage weight-stationary systolic array.

The design runs one fixed, deterministic pass after reset and then repeats it:

    IDLE -> LOAD_W (8) -> LOAD_B (8) -> COMPUTE (15) -> DRAIN (8) -> IDLE

All operands share the 4-bit field ``ui_in[3:0]`` and are signed (-8..7); the
accumulators are 8-bit signed and saturate at -128/127. Because the timing is
fully deterministic, the tests below drive the DUT purely by counting cycles
and never reach into the hierarchy -- so the same suite runs unmodified
against the gate-level netlist.

Each PE holds one stationary weight and sees the same eight activations
(staggered one cycle per stage by the data chain), so a whole pass computes:

    out[i] = saturate(bias[i] + sum(activation[k] * weight[i] for k in 0..7))
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, Timer

# --- Architecture constants (must match src/systolic_controller.sv) ----------
N_PE = 8
LOAD_W_CYCLES = 8
LOAD_B_CYCLES = 8
COMPUTE_CYCLES = 15
DRAIN_CYCLES = 8
IDLE_CYCLES = 1

CLOCK_PERIOD_US = 10
SETTLE_NS = 1  # let the combinational drain mux settle after a clock edge

ACC_MIN, ACC_MAX = -128, 127
OPERAND_MIN, OPERAND_MAX = -8, 7


def to_nibble(value):
    """Encode a signed operand into the 4-bit input field."""
    return value & 0xF


def to_signed8(value):
    """Decode the 8-bit unsigned output port into a signed accumulator value."""
    return value - 256 if value & 0x80 else value


def saturate(value):
    return max(ACC_MIN, min(ACC_MAX, value))


def golden_model(weights, biases, activations):
    """Reference model, saturating after every MAC just like the hardware.

    Only the first N_PE activations reach the array; the remaining COMPUTE
    cycles exist to flush the data chain through the later stages.
    """
    results = []
    for i in range(N_PE):
        acc = biases[i]
        for activation in activations[:N_PE]:
            acc = saturate(acc + activation * weights[i])
        results.append(acc)
    return results


class SystolicArray:
    """Cycle-accurate black-box driver for one pass through the array."""

    def __init__(self, dut):
        self.dut = dut

    async def start(self):
        """Start the clock and release reset, aligned so that the next rising
        edge is cycle 0 of LOAD_W."""
        cocotb.start_soon(Clock(self.dut.clk, CLOCK_PERIOD_US, unit="us").start())
        self.dut.ena.value = 1
        self.dut.ui_in.value = 0
        self.dut.uio_in.value = 0
        self.dut.rst_n.value = 0
        await ClockCycles(self.dut.clk, 5)
        self.dut.rst_n.value = 1
        await Timer(SETTLE_NS, "ns")

    async def _step(self, value=0):
        """Advance one cycle, presenting `value` for that cycle's capture."""
        await RisingEdge(self.dut.clk)
        self.dut.ui_in.value = to_nibble(value)
        await Timer(SETTLE_NS, "ns")

    async def run_pass(self, weights, biases, activations):
        """Drive a full LOAD_W -> LOAD_B -> COMPUTE -> DRAIN pass.

        Returns the eight signed accumulator values read out during DRAIN and
        leaves the DUT realigned for a following back-to-back pass.
        """
        assert len(weights) == N_PE and len(biases) == N_PE

        for weight in weights:
            await self._step(weight)
        for bias in biases:
            await self._step(bias)
        for cycle in range(COMPUTE_CYCLES):
            await self._step(activations[cycle] if cycle < len(activations) else 0)

        results = []
        for _ in range(DRAIN_CYCLES):
            await self._step()
            assert int(self.dut.uio_oe.value) == 0, "uio must stay configured as inputs"
            results.append(to_signed8(int(self.dut.uo_out.value)))

        for _ in range(IDLE_CYCLES):
            await self._step()
        return results


def check(dut, got, expected, label):
    for i, (actual, want) in enumerate(zip(got, expected)):
        assert actual == want, (
            f"{label}: PE{i} expected {want}, got {actual}\n"
            f"  got      = {got}\n  expected = {expected}"
        )
    dut._log.info(f"{label}: {got}")


@cocotb.test()
async def test_reset(dut):
    """Reset clears every accumulator and parks the unused IOs."""
    array = SystolicArray(dut)
    await array.start()

    assert int(dut.uo_out.value) == 0, "accumulators must be zero out of reset"
    assert int(dut.uio_out.value) == 0, "uio_out is unused and must read zero"
    assert int(dut.uio_oe.value) == 0, "uio must stay configured as inputs"


@cocotb.test()
async def test_weight_loading(dut):
    """Every weight lands in its own PE, and DRAIN reads the PEs back in order."""
    array = SystolicArray(dut)
    await array.start()

    weights = [1, 2, 3, 4, 5, 6, 7, -8]
    # A single unit activation makes each PE compute 0 + 1*w[i], so the drained
    # values are the loaded weights themselves.
    activations = [1] + [0] * (N_PE - 1)

    got = await array.run_pass(weights, [0] * N_PE, activations)
    check(dut, got, weights, "weight loading")


@cocotb.test()
async def test_bias_loading(dut):
    """Every bias lands in its own PE and survives a pass with zero weights."""
    array = SystolicArray(dut)
    await array.start()

    biases = [-8, -3, -1, 0, 1, 3, 6, 7]
    got = await array.run_pass([0] * N_PE, biases, [0] * N_PE)
    check(dut, got, biases, "bias loading")


@cocotb.test()
async def test_mac_computation(dut):
    """Directed multiply-accumulate against hand-computed results."""
    array = SystolicArray(dut)
    await array.start()

    weights = [1, 2, 3, 4, 5, 6, 7, 1]
    biases = [0, 1, 2, 3, -1, -2, -3, 0]
    activations = [1, 2, 3, -1, 0, 0, 0, 0]  # sums to 5

    # out[i] = bias[i] + 5*weight[i]
    expected = [5, 11, 17, 23, 24, 28, 32, 5]
    assert golden_model(weights, biases, activations) == expected, (
        "reference model disagrees with the hand-computed result"
    )

    got = await array.run_pass(weights, biases, activations)
    check(dut, got, expected, "mac computation")


@cocotb.test()
async def test_signed_arithmetic(dut):
    """Negative weights, activations and biases all propagate correctly."""
    array = SystolicArray(dut)
    await array.start()

    weights = [-1, -2, -3, -4, 1, 2, 3, 4]
    biases = [5, 5, 5, 5, -5, -5, -5, -5]
    activations = [3, 3, -2, 0, 0, 0, 0, 0]  # sums to 4

    expected = golden_model(weights, biases, activations)
    assert expected == [1, -3, -7, -11, -1, 3, 7, 11]

    got = await array.run_pass(weights, biases, activations)
    check(dut, got, expected, "signed arithmetic")


@cocotb.test()
async def test_accumulator_saturation(dut):
    """The accumulator clamps at both ends instead of wrapping."""
    array = SystolicArray(dut)
    await array.start()

    # 7 + 8*(7*7) = 399 -> clamps to +127
    got = await array.run_pass([7] * N_PE, [7] * N_PE, [7] * N_PE)
    check(dut, got, [ACC_MAX] * N_PE, "positive saturation")

    # -8 + 8*(7*-8) = -456 -> clamps to -128
    got = await array.run_pass([-8] * N_PE, [-8] * N_PE, [7] * N_PE)
    check(dut, got, [ACC_MIN] * N_PE, "negative saturation")


@cocotb.test()
async def test_datasheet_example(dut):
    """The worked example from docs/info.md must hold on real hardware."""
    array = SystolicArray(dut)
    await array.start()

    weights = [2, 3, 4, 5, 6, 7, 1, 2]
    activations = [7] * N_PE  # eight times the maximum 4-bit signed value

    expected = [112, ACC_MAX, ACC_MAX, ACC_MAX, ACC_MAX, ACC_MAX, 56, 112]
    assert golden_model(weights, [0] * N_PE, activations) == expected

    got = await array.run_pass(weights, [0] * N_PE, activations)
    check(dut, got, expected, "datasheet example")


@cocotb.test()
async def test_back_to_back_passes(dut):
    """The FSM returns to IDLE and reloads cleanly for the next pass.

    This is the regression test for the DRAIN state failing to terminate.
    """
    array = SystolicArray(dut)
    await array.start()

    passes = [
        ([1, 2, 3, 4, 5, 6, 7, 1], [0] * N_PE, [1, 1, 0, 0, 0, 0, 0, 0]),
        ([-1, -2, -3, 1, 2, 3, -4, 4], [1, 2, 3, 4, -1, -2, -3, -4], [2, -1, 1, 0, 0, 0, 0, 0]),
        ([2] * N_PE, [7] * N_PE, [1, 2, 3, 4, 5, 6, 7, -8]),
    ]

    for index, (weights, biases, activations) in enumerate(passes):
        got = await array.run_pass(weights, biases, activations)
        check(dut, got, golden_model(weights, biases, activations), f"pass {index}")


@cocotb.test()
async def test_randomized_passes(dut):
    """Randomized regression against the reference model."""
    array = SystolicArray(dut)
    await array.start()
    rng = random.Random(0xC0FFEE)

    def operand():
        return rng.randint(OPERAND_MIN, OPERAND_MAX)

    for index in range(25):
        weights = [operand() for _ in range(N_PE)]
        biases = [operand() for _ in range(N_PE)]
        # Cycles past the eighth only flush the data chain, so fill them with
        # noise to confirm they cannot disturb the results.
        activations = [operand() for _ in range(COMPUTE_CYCLES)]

        got = await array.run_pass(weights, biases, activations)
        check(dut, got, golden_model(weights, biases, activations), f"random {index}")
