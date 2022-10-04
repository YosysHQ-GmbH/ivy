# InVariants with Yosys (IVY)

IVY is a file-format for describing (inductive) invariants of SystemVerilog designs,
and strategies for proving them, and a tool for working with those files.

The IVY tool
- can formally prove the invariants to be correct,
- can help mining invariants from the design semi-automatically,
- can help manage invariants and keep track of which are proven and which aren't, and
- can generate SV code to use the invariants to refine proofs of other properties.

Because IVY ultimatey produces a SystemVerilog constraints file it is possible
to use IVY invariant files with virtually any tool for formal verification of
SystemVerilog designs.

## IVY File Format

The IVY file fomat is simply an extension to SystemVerilog, adding `invariant .. endinvariant`
and `proof .. endproof` blocks to the language. Those statements can either be used directly
within the unit under test, optionally guarded by an `` `ifdef ``, or they can be put in an
extra file that hooks into the unit under test using the `bind()` statement.

### Invariant-Endinvariant Blocks

An invariant consists of an optional clocking block, and a comma-seperated list of invariant expressions.
Invariant expressions are Verilog expressions that may contain the new `=>` operator described below.

```
invariant foobar;
  @(posedge clock) disable iff (reset)   // or default clocking
  X -> Y, Z,                             // Equivalent to (!X || Y) && Z
  A -> B => C -> D;                      // Expression with new `=>` operator
endinvariant
```

#### The infix and prefix => operators

Note that `=>` is already used as operator in the specify path and coverage point parts of the SV language. Neither
conflicts with the use of this operator in `invariant` expressions.

The infix `=>` operator has the same precedence and associativity as `->` and `<->` (right associative).

The semantic of `=>` is similar to that of `|=>`, except that `=>` checks the consequent part immediately after the clock event,
whereas `|=>` waits for the next clock event and then checks the values sampled by that next clock event.

Thus `X => Y` is basically equivalent to `X |-> $future_gclk(Y)` in an SVA property,
iff the global clock includes all clock events that can result in a change of the value of `Y`.
Like with the `$future_glck()` function, it is also illegal to nest instances of the `=>` operator.

The prefix `=>` operator evaluates to stable value of the argument after the clock event.
Thus `=> Y` is equivalent to `$future_gclk(Y)` (under the same conditions as above).

#### $past, $stable, $rose, $fell in invariant expressions



### Proof-Endproof Blocks

```
proof proof_1;
  config depth = 8;
endproof
```

Some existing SV keywords that we may want to use for one thing or another in proof blocks:  
`assert assume restrict cover automatic before config disable constraint cross expect extends
force global local implements implies inside matches priority property pure release solve static
super table tagged task use virtual wildcard with within`

## Example IVY Project

```
invariant quux;
  @(posedge clock) disable iff (reset)
  control42 ? (A -> B => C) : (X -> Y => Z);
endinvariant

invariant quuxbar;  // just a name for the intersection of "foobar" and "quux"
  foobar, quux;
endinvariant

proof alu_proof;   // prove (SVA) alu abstraction properties with k-induction
  blackbox lsu;
  assert property (alu_abstraction_prop1);
  assert property (alu_abstraction_prop2);
  assert property (alu_abstraction_prop3);
  method "kind(depth=8)";
endproof

proof invar_proof;   // prove invariants with k-induction
  blackbox alu, lsu;
  assume proof (alu_proof);   // assume everything asserted in proof alu_proof
  assert invariant (foobar);
  assert invariant (quux);
  method "kind(depth=12)";
endproof

proof req_resp_proof;   // prove request-response property with IPC
  cutpoint reg_bypass_value;
  assume invariant (quuxbar);  // implicit link to invar_proof (or verification error if unresolvable)
  assert property (req_resp_check);
  method "ipc(depth=27)";
endproof

assert proof (req_resp_proof);  // implies all other proofs up in the dependency chain
```

Arguments to proofs and invariants are formal arguments, similar to arguments to properties and sequences,
and should be resolved by the SV front-end using the same mechanism.
