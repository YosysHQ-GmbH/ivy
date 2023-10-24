Getting Started
===============

.. default-role:: code

Installation
------------

IVY is part of the `Tabby CAD Suite`_, which also contains all required dependencies.

Currently IVY requires Tabby CAD for the IVY language support.
This section will be extended when it becomes possible to use IVY's tooling with an alternative way of specifying invariants that is compatible with the `OSS CAD Suite`_.

.. _`Tabby CAD Suite`: https://www.yosyshq.com/tabby-cad-datasheet
.. _`OSS CAD Suite`: https://github.com/YosysHQ/oss-cad-suite-build

Project Setup
-------------

IVY's primary use case requires a design with some properties that should be formally verified. Ideally you already have an SBY_ project setup (even if the verification using SBY does not actually succeed).

.. _SBY: https://yosyshq.readthedocs.io/projects/sby/en/latest/

Example Design
~~~~~~~~~~~~~~

For this getting started guide, we are going to use the `split_fifo` example design contained in IVY's `examples` directory. This is included in Tabby CAD's `examples` directory and can also be found on GitHub_.

.. _GitHub: https://github.com/YosysHQ-GmbH/ivy/tree/main/examples/split_fifo

This example contains a design that takes pair of requests and processes them in two independent lanes.
Each requests can take a variable number of cycles, so each lane has an input and output FIFO to buffer single requests or responses.
Additionally the top-level module contains a unified input and output FIFO each storing pairs of requests or responses.

The SystemVerilog description of the design is contained in the `splif_fifo.sv` file.

The following diagram shows the structure of the design:

.. graphviz::

   digraph {
      node [shape=rect];
      rankdir = "LR";

      data_in [shape=ellipse];
      data_in -> input_fifo;

      subgraph cluster_top {
         label = "top";

         input_fifo;

         input_fifo -> in_fifo0;
         input_fifo -> in_fifo1;

         subgraph cluster_lane0 {
            label = "lane[0]";

            in_fifo0 [label = "in_fifo"];

            in_fifo0 -> multiplier0;

            multiplier0 [label = "multiplier"];

            multiplier0 -> out_fifo0;

            out_fifo0 [label = "out_fifo"];
         }

         subgraph cluster_lane1 {
            label = "lane[1]";

            in_fifo1 [label = "in_fifo"];

            in_fifo1 -> multiplier1;

            multiplier1 [label = "multiplier"];

            multiplier1 -> out_fifo1;

            out_fifo1 [label = "out_fifo"];
         }

         out_fifo0 -> output_fifo;
         out_fifo1 -> output_fifo;

         output_fifo;
      }
      output_fifo -> data_out;

      data_out [shape=ellipse];
   }


The individual modules use `valid` and `ready` signals for each `data` signal. The `valid` signal is driven by the same module as the `data` signal and the `ready` signal by the module reading the `data` signal. In every cucle where the `valid` signal is asserted `data` is valid and remains stable as long as `valid` is asserted. A transfer happens exactly in every cycle where both `valid` and `ready` are asserted.

For the `top` module this looks as follows

.. literalinclude:: ../../examples/split_fifo/split_fifo.sv
   :language: systemverilog
   :start-at: module top
   :end-at: );

On the top-level the input data signals contain two pairs of numbers, one pair per lane with each number represented using `WIDTH` bits.
The multipliers each take a pair of numbers and produce their product as a `WIDTH*2` bit number.
Whenever both lanes have an entry in their output FIFO, those entries are combined into a single entry of the top-level ouptut FIFO.

Note that, since the multipliers used in this example take a variable and data-dependent number of cycles, the number of entries in the per-lane input FIFOs may differ even though entries can only enter those input FIFOs simultaneously.
Similarly, the number of entries in the per-lane output FIFOs can differ.

Deadlocks common concern when combining several modules and FIFO buffers in a topology like this where the data flow splits and recombines.
As part of ensuring that our example design will not deadlock, we want to verify the following property included in the top module:


.. literalinclude:: ../../examples/split_fifo/split_fifo.sv
   :language: systemverilog
   :start-at: property progress;
   :end-at: endproperty

It says that whenever an input request enters, we get a valid output response within the next 9 cycles. Note that we don't require the output to be the one corresponding for the given input, as that's not required to ensure progress.

SBY Project
~~~~~~~~~~~

The example comes with an SBY project (`split_fifo.sby`) that reads the design, ensures the design is initially reset, and asserts the property from the desing:

.. literalinclude:: ../../examples/split_fifo/split_fifo.sby
   :language: text
   :start-at: [script]

Before that, the `.sby` file configures prove task using two different engines:

.. literalinclude:: ../../examples/split_fifo/split_fifo.sby
   :language: text
   :end-before: [script]

The engines are `smtbmc` which performs k-induction and `abc pdr` which uses the PDR/IC3 algorithm which can also prove some properties which are not k-inductive for any reasonably bounded k.

If we run the `smtbmc` task using `sby -f split_fifo.sby smtbmc` it will finish within a few seconds, but SBY tells us that our property is not k-inductive for the specified depth of 20:

.. code-block:: text

   summary: Elapsed clock time [H:MM:SS (secs)]: 0:00:05 (5)
   summary: Elapsed process time [H:MM:SS (secs)]: 0:00:06 (6)
   summary: engine_0 (smtbmc) returned pass for basecase
   summary: engine_0 (smtbmc) returned FAIL for induction
   summary: counterexample trace [induction]: split_fifo_smtbmc/engine_0/trace_induct.vcd
   summary:   failed assertion top.asserts.progress at assert.sv:7.15-7.46 in step 0
   DONE (UNKNOWN, rc=4)

Since a depth of 20 far exceeds the number of cycles considered by our property as well as the overall latency of our design, it is unlikely that increasing the depth will allow us to make progress. If we want to use k-induction we will need to use additional design invariants.

At this point we might try to switch to `abc pdr` since it can handle some properties that are, like ours, not k-inductive for any practical k.
For this particular property, though, it is not effective so after waiting a few minutes we interrupt SBY.
(The `depth` option is not used for the `abc pdr` engine, so it will not exit unless it can find a counter-example or verify the property.)

Creating an IVY Project
~~~~~~~~~~~~~~~~~~~~~~~

Since we need additional invariants, we decide to use IVY to manage these. Based on the SBY project, we can create the following IVY project (`split_fifo.ivy`):

.. literalinclude:: ../../examples/split_fifo/split_fifo.ivy

For IVY the `[script]` section used in SBY is split into two parts.
A `[read]` section and a `[script]` section. The `[read]` section contains all `read` or `verific` commands preceding any `prep`, `hierarchy` or `verific -import` commands.
The `[script]` section contains all following commands and can be omitted if it consists of only `prep -top <top>`.
Where `<top>` is replaced by the top module specified in the `[options]` section.
Even when we do provide a custom `[script]` section, we still need to tell `IVY` the name of the top level module using the `top` option.

The `[files]` and `[file <filename>]` sections work like they do in SBY.

In this case we decided to place the invariants in the separate file `split_fifo_invariants.svi`.
We could also place them inside the design files themselves, but that may require guarding them in `\ `ifdef IVY` or similar directives to use the same files outside of IVY.
Note that IVY does not automatically set any such defines, so this would also require `read -define IVY` at the start of the `[read]` section.

Creating an Invariants File
~~~~~~~~~~~~~~~~~~~~~~~~~~~

We start writing the `split_fifo_invariants.svi` file by adding a module that will contain invariants for the `top` module and using a bind statement to instantiate it as part of the `top` module:

.. literalinclude:: ../../examples/split_fifo/quickstart/split_fifo_invariants_a.svi

We also add an assumption to, again, ensure the design is initially reset.
Now we encounter the first use of IVY's SystemVerilog extensions.
The `automatic proof progress_p; ... end proof` block defines a proof task.
A proof task can contain assertions of invariants and properties, assumptions of invariants and additional configuration.
By default IVY will run all proof tasks which are declared using `automatic proof` or which are explicitly targeted by `solve proof` statements.
(In case an `automatic proof` has parameters IVY will only run instaces of it using parameter values given by other active proof tasks.)

When running proof tasks, IVY will keep track of their verification status and continuously updates which invariants are fully proven and which invariants still depend on unproven assumptions, including transitive assumptions or cyclic delayed/inductive assumptions.

So far, for our example, we declared a proof task that asserts our target property without assuming any invariants.
We also configured the proof task to use a depth of 10, since within IVY the default depth is 5, which is not sufficient for our property spanning 9 cycles.

Given that we have not added any invariants yet, we cannot expect IVY to be able to prove the property, but we can verify the project setup by running `ivy -f split_fifo.ivy`:


.. code-block:: text

   Copy 'split_fifo.sv' to 'split_fifo/src/split_fifo.sv'
   Copy 'split_fifo_invariants.svi' to 'split_fifo/src/split_fifo_invariants.svi'
   export: starting process (cd split_fifo/src && yosys -ql ../ivy_export.log ../ivy_export.ys)
   export: finished (returncode=0)
   Scheduling proof task invs.progress_p
   design: starting process (cd split_fifo/model && yosys -ql design.log design.ys)
   design: finished (returncode=0)
   invs.progress_p: starting process (cd split_fifo/tasks && sby -f invs.progress_p.sby)
   invs.progress_p: finished (returncode=4)
   invs.progress_p: Elapsed clock time [H:MM:SS (secs)]: 0:00:00 (0)
   invs.progress_p: Elapsed process time [H:MM:SS (secs)]: 0:00:01 (1)
   invs.progress_p: engine_0 (smtbmc) returned pass for basecase
   invs.progress_p: engine_0 (smtbmc) returned FAIL for induction
   invs.progress_p: counterexample trace [induction]: engine_0/trace_induct.vcd
   invs.progress_p:   failed assertion top.ivy_property_progress at  in step 0
   Proof status:
     proof invs.progress_p: unknown
     property progress: unknown


(This in-progress state is included in the `quickstart` subdirectory and can be run with `ivy -f quickstart/split_fifo_a.ivy`.)

We can output the finaly `Proof status:` output again without re-running the proof tasks by using the `ivy -f split_fifo.ivy status` command.
This command can also be run while another long-running IVY process is running proof tasks to output the current verification status of all active invariants, properties and proof tasks.

Finding Useful Invariants
-------------------------

When k-induction fails, we get an induction counter-example as a trace.
Unlike actual counter-examples to the property, this trace does not start it the initial state but can start in an arbitrary state.
If the property actually holds, that state will be an unreachable state, but k-induction (for a fixed k) is not able to show this.

To be able to prove our target property using k-induction, we need to exclude these unreachable states.
We can do this by adding and independently verifying another property that excludes the unreachable states not handled by k-induction.
In theory, this can always be done using non-temporal properties that only look at a single state or a single state transition at a time.
Often this is also a good strategy in practice, as these properties are simpler to reason about and directly characterize non-reachable states or non-reachable state transitions.
To distinguish these properties from the properties we want to ultimately verify, we call them "invariants" as they hold unconditionally in any reachable state and are maintained by state transitions.

If we look at the trace produced when running IVY for our example, it will take some effort to see why the initial state of the trace should be unreachable.
We can simplify this task by adding helper signals via `bind` statements.
For the example design the fill level of the individual FIFOs gives a good overview of what is going on, but the actual FIFO implementation does not explicitly keep track of the fill-level as it is implemented purely by operations on read and write pointers.
Thus like for the `top` module we declare an invariant and helper-signal module for a FIFO and bind it to all FIFO instances:

.. highlight:: systemverilog

.. literalinclude:: ../../examples/split_fifo/quickstart/split_fifo_invariants_b.svi
   :language: systemverilog
   :end-at: ) invs (.*);

Since we aren't using the `level` wire anywhere yet, we add a `(* keep *)` signal to ensure it will always be included in the generated traces:

.. wavedrom:: quickstart/trace_b.json

The exact trace produced might change between versions, but in any case it should show a mismatching total number of entries between the two lanes.
The total number here potentially includes the one currently being processed in the multiplier, when its `in_ready` signal is low.
Since entries can only enter and leave both lanes simultaneously, such a state should not be reachable.

To exclude this we will first declare a helper `wire` that keeps track of the this total number of in-flight requests:

.. literalinclude:: ../../examples/split_fifo/quickstart/split_fifo_invariants_c.svi
   :language: systemverilog
   :start-at: module lane_invariants
   :end-at: ) invs (.*);

Then we add an invariant in the `top_invariants` module that states that this number is always the same for both lanes:

.. literalinclude:: ../../examples/split_fifo/quickstart/split_fifo_invariants_c.svi
   :language: systemverilog
   :start-at: invariant same_in_flight;
   :end-at: endinvariant

We can also try to prove this invariant without any further assumptions by adding the following `automatic proof` block right below it:

.. literalinclude:: ../../examples/split_fifo/quickstart/split_fifo_invariants_c.svi
   :language: systemverilog
   :start-at: automatic proof same_in_flight_p;
   :end-at: endproof

Finally we change the `proof` block for our property to include this invariant as assumption:

.. literalinclude:: ../../examples/split_fifo/quickstart/split_fifo_invariants_c.svi
   :language: systemverilog
   :start-at: automatic proof progress_p;
   :end-at: endproof

If we re-run IVY we get the following proof status summary:

.. code-block:: text

   Proof status:
     invariant invs.same_in_flight: unknown
     proof invs.progress_p: unknown
     proof invs.same_in_flight_p: unknown
     property progress: unknown

This might look like we made no progress, to see the effect of our changes, we need to look at the new induction counter-example for the `invs.progress_p` proof task:

.. wavedrom:: quickstart/trace_c.json

We see that now the `in_flight` values stays the same, so we successfully excluded the previously seen states.
The `invariant invs.same_in_flight: unknown` line, though, tells us that we haven't shown that these excluded states are actually unreachable, but we can finish dealing with our target property first, as IVY will keep track of what is still left to prove.

We can also see that the output FIFO of `lane[0]` and the input FIFO of `lane[1]` overflow.
This happens as they start out with a fill level (as determined by their read and write pointers) that exceeds the actual capacity of the FIFO.
Such a state is also not reachable unless there is an actual bug in our FIFO implementation and it is not surprising that an overflowing FIFO causes our target property to be violated.

To exclude states where FIFOs can overflow, we will add another invariant.
This invariant will target a single FIFO module and assert that the level does not exceed the capacity:

.. literalinclude:: ../../examples/split_fifo/quickstart/split_fifo_invariants_d.svi
   :language: systemverilog
   :start-at: module fifo_invariants #(DEPTH_BITS);
   :end-at: endmodule

To use this invariant in our top-level proof, we can add an intermediate `proof` block to `lane_invariants`:

.. literalinclude:: ../../examples/split_fifo/quickstart/split_fifo_invariants_d.svi
   :language: systemverilog
   :start-at: proof level_max_p;
   :end-at: endproof

Since this `proof` block doesn't `assert` anything, it's not strictly speaking a proof and IVY will not schedule a task for it, but in IVY `proof` blocks can also be used to group related invariants via the `export` statement.
This allows us to assume such a group of invariants with a single `use proof` statement without repeating the list of them every time they are used.

.. literalinclude:: ../../examples/split_fifo/quickstart/split_fifo_invariants_d.svi
   :language: systemverilog
   :start-at: proof fifo_level_max;
   :end-at: endproof

Our updated top-level proof for our property now looks like this:

.. literalinclude:: ../../examples/split_fifo/quickstart/split_fifo_invariants_d.svi
   :language: systemverilog
   :start-at: automatic proof progress_p;
   :end-at: endproof

Re-runing IVY now produces this summary:

.. code-block:: text

   Proof status:
     invariant input_fifo.invs.level_max: pass
     invariant invs.same_in_flight: unknown
     invariant lane[0].in_fifo.invs.level_max: pass
     invariant lane[0].out_fifo.invs.level_max: pass
     invariant lane[1].in_fifo.invs.level_max: pass
     invariant lane[1].out_fifo.invs.level_max: pass
     invariant output_fifo.invs.level_max: pass
     proof input_fifo.invs.level_max_p: pass
     proof invs.progress_p: unknown (task pass)
     proof invs.same_in_flight_p: unknown
     proof lane[0].in_fifo.invs.level_max_p: pass
     proof lane[0].out_fifo.invs.level_max_p: pass
     proof lane[1].in_fifo.invs.level_max_p: pass
     proof lane[1].out_fifo.invs.level_max_p: pass
     proof output_fifo.invs.level_max_p: pass
     property progress: unknown

We still have `property progress: unknown`, so we aren't quite done yet, but we also see `proof invs.progress_p: unknown (task pass)`.
Here `(task pass)` means IVY could successfully verify `proof progress_p` itself, but the asserted property remains `unknown` due to unverified assumptions used by `proof progress_p`.
Since all instances of the FIFO level invariant could be verified automatically, this leaves our `same_in_flight` assumption.

If we inspect the produced induction counter-examples for the `same_in_flight` invariant, it turns out the only thing preventing IVY from proving the invariant are, again, states where FIFOs overflow, so after adding the same `use proof` statements its `proof` block, we can finally fully verify our target property:

.. literalinclude:: ../../examples/split_fifo/split_fifo_invariants.svi
   :language: systemverilog
   :start-at: automatic proof same_in_flight_p;
   :end-at: endproof

.. code-block:: text

   Proof status:
     invariant input_fifo.invs.level_max: pass
     invariant invs.same_in_flight: pass
     invariant lane[0].in_fifo.invs.level_max: pass
     invariant lane[0].out_fifo.invs.level_max: pass
     invariant lane[1].in_fifo.invs.level_max: pass
     invariant lane[1].out_fifo.invs.level_max: pass
     invariant output_fifo.invs.level_max: pass
     proof input_fifo.invs.level_max_p: pass
     proof invs.progress_p: pass
     proof invs.same_in_flight_p: pass
     proof lane[0].in_fifo.invs.level_max_p: pass
     proof lane[0].out_fifo.invs.level_max_p: pass
     proof lane[1].in_fifo.invs.level_max_p: pass
     proof lane[1].out_fifo.invs.level_max_p: pass
     proof output_fifo.invs.level_max_p: pass
     property progress: pass


