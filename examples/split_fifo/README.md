# Split FIFO Example

This example contains a design that takes pair of requests and processes them
in two independent lanes. Each requests can take a variable number of cycles,
so each lane has an input and output FIFO to buffer single requests or
responses. Additionally the top-level module contains a unified input and
output FIFO each storing pairs of requests or responses.

The example property we want to verify is that whenever the design accepts a
request, it will produce a response within a given number of cycles.

The design and the property are defined in [`split_fifo.sv`](split_fifo.sv). The property definition is at the bottom.

If we try to use SBY to verify the design (see [`split_fifo.sby`](split_fifo.sby)) and we are using smtbmc's k-induction (`sby -f split_fifo.sby smtbmc`) we will see that the property is not k-inductive. If we try to use abc's PDR (`sby -f split_fifo.sby pdr`) we can observe that it will not finish within a reasonable amount of time.

Using IVY we are able to break up the verification problem into smaller steps by proving design invariants which we can then use to prove further invariants and eventually the final property. IVY keeps track of the dependencies between invariants and properties, schedules solver tasks to verify each proof step and tracks the solving progress.

IVY is configured using a SystemVerilog extension that adds `proof` and `invariant` statements. In this example we keep the configuration separate from the design and use SystemVerilog's `bind` functionality to attach the required invariants to our design. This is done in [`split_fifo_invariants.svi`](`split_fifo_invariants.svi`).

To perform verification of all defined proof steps, run `ivy -f split_fifo.ivy`. This will fully verify the target property together with all required invariants within seconds.

If you comment out an assumption of a proof step, running IVY will produce an output where some proofs have the status `unknown`. In a scenario similar to this, but where we don't already know the required invariant, we can look in the `tasks` subdirectory of IVY's work directory to find a trace that is an induction counterexample for each proof step having an `unknown` status.
