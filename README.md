# InVariants with Yosys (IVY)

The IVY defines SystemVerilog langage extensions for describing (inductive) invariants of
SystemVerilog designs, for strengthening proofs of SVA properties, and for describing strategies
for proving those invariants and SVA properties.

IVY can be thought of as a proof-assistent for digital circuits and formal safety properties.

The IVY tool
- can formally prove invariants and SVA properties to be correct,
- can help mining invariants from the design semi-automatically,
- can help manage invariants and keep track of which are proven and which aren't, and
- can generate SystemVerilog SVA code for assuming the proven invariants.

Because IVY can produce SystemVerilog constraints files for the proven invariants, it is possible
to use IVY invariant files to strengthen proofs performed with virtually any tool for formal
verification of SystemVerilog designs.

## IVY File Format

The IVY file fomat is simply an extension to SystemVerilog, adding `invariant .. endinvariant`
and `proof .. endproof` blocks to the language. Those statements can either be used directly
within the unit under test, optionally guarded by an `` `ifdef ``, or they can be put in an
extra file that hooks into the unit under test using the `bind()` statement.

Proofs and invariants support formal arguments, similar to arguments to properties and sequences,
and should be resolved by the SV front-end using the same mechanism.

### Invariant-Endinvariant Blocks

An invariant consists of an optional clocking block, and a comma-seperated list of invariant expressions.
Invariant expressions are Verilog expressions that may contain the new `=>` operator described below.

```SystemVerilog
invariant foobar;
  @(posedge clock) disable iff (reset)   // or default clocking
  X -> Y, Z,                             // Equivalent to (!X || Y) && Z
  A -> B => C -> D;                      // Expression with new `=>` operator
endinvariant
```

#### The infix and prefix => operators

> Note that `=>` is already used as operator in the specify path and coverage point parts of the SV language.
> Neither conflicts with the use of this operator in `invariant` expressions.

The infix `=>` operator has the same precedence and associativity as `->` and `<->` (right associative).

The semantic of `=>` is similar to that of `|=>`, except that `=>` checks the consequent part immediately after the clock event,
whereas `|=>` waits for the next clock event and then checks the values sampled by that next clock event.

Thus `X => Y` is basically equivalent to `X |-> $future_gclk(Y)` in an SVA property,
iff the global clock includes all clock events that can result in a change of the value of `Y`.
Like with the `$future_glck()` function, it is also illegal to nest instances of the `=>` operator.

The prefix `=>` operator has the same precedence as the infix `=>` operator and evaluates to the stable value of the argument
after the clock event. Thus `=> Y` is equivalent to `$future_gclk(Y)` (under the same conditions as above). Note that the
prefix `=>` operator evaluates to whatever type its argument evaluates to, whereas the infix `=>` operator always evaluates to
a single bit logic value.

#### $rose, $fell, $stable, $changed, and $past in invariant expressions

The sampled value functions `$rose()`, `$fell()`, `$stable()`, `$changed()`, and `$past()`
can be used in the right hand side of an infix or prefix `=>` operator, but only without
explicit clocking event argument. For example:

```SystemVerilog
invariant regA;
  @(posedge clock) !EN => $stable(Q)
endinvariant
```

Which is equivalent to writing:

```SystemVerilog
invariant regA;
  (=> $rose(clock)) -> !EN => $stable(Q)
endinvariant
```

### Proof-Endproof Blocks

```SystemVerilog
// define a new proof. all statements from other proofs listed in the "extends" declaration are
// effectively copied into this proof before the statements in the body of this proof declaration.
proof my_proof extends base_proof;
  use property property_1;     // assume an SVA property (that needs to be proven independently)
  use invariant invariant_1;   // assume an invariant (that needs to be proven independently)
  use proof proof_1;           // assume every invariant and property asserted by another proof

  assert property property_2;       // prove an SVA property
  assert invariant invariant_2;     // prove an invariant

  // Use "inside" and "disable" to whitelist and blacklist parts of the design hierarchy.
  // Connections between "inside" blocks, and those blocks and top-level module ports, are preserved,
  // as well as invariants and properties contraining those signals.
  inside cpu;       // blackboxes everything outside of the given entity (may list multiple entities)
  disable cpu.alu;  // blackbox a part of the design hierarchy

  // An "implements" statement has no effect on the proof that contains it, but any proof using this
  // proof (with a "use" statement) will implicitly blackbox the entities specified in the "implements"
  // clause in this proof. This can be used to easily prove and use abstractions.
  implements cpu.regfile;

  // Key-value pairs to configure the solver. These are "vendor specific", and if another vendor would
  // support the same language extension, they would likely implement a different set of config switches.
  config depth = 8;
  config method = "k-induction";
  config engine = "smtbmc yices";
endproof

// A top-level proof that doesn't assert anything, just uses the set of proofs that should be proven,
// can be used to organize proofs. If any proofs are "use"-ing properties or invariants directly, then
// IVY will check that those are asserted by at least one proof that is referenced in the hierarchy below
// the top proof.
proof top_proof;
  use my_proof;
  use another_proof(42, "darkstar");
endproof
```

TBD: Using "constraint" expressions and "table" statements, or something similar, to construct some
mechanism for partitioning cases.

TBD: Some kind of "automatic use" combined with generate-for-loops to create lists of proofs the tool
can pick from to prove the properties and/or invariants that proofs (anywhere in the design hierarchy)
are "use"-ing directly.

> Some existing SV keywords that we may want to re-use for one thing or another in proof blocks:  
`assert assume restrict cover automatic before config disable constraint cross expect extends
force global local implements implies inside matches priority property pure release solve static
super table tagged task use virtual wildcard with within`

## Example IVY Project

TBD

## Updated Semantics of statements in proof..endproof blocks

### `assert invariant|sequence|property <name>;`

Assert the specified properties. If `assume`, `use`, etc is used on this proof, all the properties asserted here will be assumed in that other proof.

### `assume invariant|sequence|property <name>;`

Assume the specified properties. They must be proven independently.

### `assume|extends|use proof <name>;`

Assume the properties asserted in the other proof. With `use` we also `disable` everything that the other proof has listed as `implements`.
Extends `extends` is similar to `use` but also re-exports the assumed properties and disabled entities to every other proof that uses `extends` or
`use` on the proof that contains the `extends` clause.

### `cross assume|use invariant|sequence|property|proof <name>;`

Like ordinary `assume` or `use`, but only assume the other properties in the prior, so that two properties can use each other in their induction proofs.

### `import proof <name>;`

Essentialy copy all statements from the other proof into this proof, using the other proof like a template. A proof that is imported like that into another proof doesn't need to be proven by itself.

## Other changes to SystemVerilog

Add support for `solve proof <name>;` in module-context to specify the "root proofs" to use. The special statement `solve proof automatic;` will assume a `solve proof` statement for all proofs without arguments in the same module.
