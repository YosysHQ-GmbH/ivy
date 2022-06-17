One way of implementing the functionality of IVY is to extend the SystemVerilog language.
It makes sense to keep the language extension as compact as possible.
For example by only adding `invariant .. endinvariant` and `proof .. endproof` with a syntax like this:

```
`ifdef YOSYS
  invariant foobar;
    @(posedge clock) disable iff (reset)   // or default clocking
    if (control_flag)                      // condition
      bar |-> !foo,                        // simple same-cyce invariant (same as "if (bar) foo")
      foo |=> fsm_state == 13;             // can look max one cycle ahead (rhs expr should not
                                           // depend on any input values, only previous state)
  endinvariant

  invariant quux;
    @(posedge clock) disable iff (reset)
    if (!control_flag)
      foo == bar,
      reg_A != reg_B;
  endinvariant

  proof myproof;
    blackbox alu, lsu;             // instruct the tool to blackbox the given entities for this proof
    cutpoint debug_port_data;      // instruct the tool to use the given cut-point for running the proof
    assume alu_abstraction;        // assume the given property or invariant (must be proven independently)
    assert foobar, quux;           // invariants foobar and quux combined form an inductive set
  endproof
  
  assert proof (myproof);
`endif
```

Any arguments to proofs and invariants are formal arguments that should be resolved by the SV front-end
in the same way it already resolves formal arguments to properties, sequences, and let-statements.
