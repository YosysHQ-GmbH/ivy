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

### Minimal Viable Product

```
[automatic] proof <name>;
    [local] assert invariant <name>;
    [cross] assume invariant|proof <name>;
    solve proof <name>;
    solve invariant|sequence|property <name>;
    solve with "<solver>";
endproof

solve proof <name>;
solve invariant|sequence|property <name>;
```

### Assert-Assume

#### `[local] assert invariant|sequence|property <name>;`

Assert the specified properties. If the proof suceeds, the properties will be considered proven.

#### `[cross] assume invariant|sequence|property <name>;`

Assume the specified properties. They must be proven independently.

With `cross` the speciefied properties are only assumed in the prior, not the final state. This allows proofs to assume each others asserted properties.

#### `[cross] assume proof <name>;`

Assume all properties that are asserted in the specified proof, unless they are asserted with `local`.

#### `export [cross] [assert|assume] invariant|sequence|property|proof <name>;`

Export the specified property. Any proof `use`ing this one will assume the specified properties.

(Also `assert`, `assume`, or `cross assume` the specified properties as indicated.)

#### `[export] use proof <name>;`

Assume the properties exported in the other proof.

### Proof Management

#### `[automatic] proof ... endproof`

With `automatic` the proof is added to the database automatically, when it has no arguments, or when
it's used in any other proof with `use` or `assume`. Without `automatic`, the proof is only added to
the database when added explicitly with `solve proof <name>;`.

#### `solve proof <name>;`

When elaborating this proof, also elaborate the specific proof, and add it to the database.

The `solve proof` statement can also be used in module context to specify the "top-level" proofs.

#### `solve with "<solver-command>";`

Which solver to use to prove the assertions in this proof. Multiple `solve with` clauses can be specified and a tool is free to pick wichever it supports. A tool should not attempt to solve a proof that has no "solve with" clause it supports.

A proof that doesn't assert anything doesn't need a `solve with` clause, and all `solve with` clauses provided for such a proof are ignored.

### Abstractions

#### `[export] disable <entity>;`

Specify a cutpoint.

If specified with `export` then any proof `use`ing this one will inherit the cutpoint.

#### `implents <entity>;`

A cut point that other proofs will inherit but this proof is not using itself.

#### `[export] inside <entity>;`

A way to cutpoint everything except the given entitny. Can be used multiple times and mixed with `disable` statements.

If any `inside` clauses are provided, then all cells are removed that are not inside any of the entities listed, or a
direct prefix of any of the entities. I.e. with `inside top.foo;`, `inside top.bar;` we will keep the top module itself,
and the hierarchies below `top.foo` and `top.bar`, but remove all other cells in `top`. Wires connecting the remaining
cells are kept.

Disable statements are executed independent of `inside` clauses.

### Case Management

#### `assert table (<expr>)|{<expr-list>} [not] within {<const-list>};`

Prove that the const list contains at least all the possible cases (or only impossible cases) for the given expression(s).

#### `[export] [assume] table (<expr>)|{<expr-list>} [not] within {<const-list>};`

Restrict this proof to a certain case or list of cases. (The condition is only assumed in the last cycle of the witness, i.e. the cycle in which the property would fail.) IVY will keep track of the cases and make sure that a property is either proven for all cases, or is only used in cases with compatible restrictions. (Either `export` or `assume` or both must be present for the statement to be valid.)
