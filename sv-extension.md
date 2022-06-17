One way of implementing the functionality of IVY is to extend the SystemVerilog language.
It makes sense to keep the language extension as compact as possible.
For example by only adding `invariant .. endinvariant` and `proof .. endproof` with a syntax like this:

```
invariant foobar;
  @(posedge clock) disable iff (reset)   // or default clocking
  X -> Y, Z,                             // Equivalent to (!X || Y) && Z
  A -> B => C -> D;                      // Expression with new `=>` operator
endinvariant
```

The `=>` operator has the same precedence and associativity as `->` and `<->`. (That is they are right associative. ;)
Note that `=>` is already used as operator in the specify path and coverage point parts of the SV language. Neither
conflicts with the use of this operator in `invariant` expressions.

The semantic of `=>` is similar to that of `|=>`, except that `=>` checks the consequent part immediately after the clock event,
whereas `|=>` waits for the next clock event and then checks the values sampled by that next clock event.

Thus `X => Y` is basically equivalent to `X |-> $future_gclk(Y)` in an SVA property,
iff the global clock includes all clock events that can result in a change the value of `Y`.
Like with the `$future_glck()` function, it is also illegal to nest instances of the `=>` operator.

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
