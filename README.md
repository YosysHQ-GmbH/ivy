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

NOTE: The export of SVA code is currently limited to what IVY uses internally and may need to be
extended to be fully usable with third party tools.

## IVY File Format

The IVY file fomat is simply an extension to SystemVerilog, adding `invariant .. endinvariant`
and `proof .. endproof` blocks to the language. Those statements can either be used directly
within the unit under test, optionally guarded by an `` `ifdef ``, or they can be put in an
extra file that hooks into the unit under test using the `bind()` statement.

Proofs and invariants support formal arguments, similar to arguments to properties and sequences,
and should be resolved by the SV front-end using the same mechanism.

### Invariant-Endinvariant Blocks

An invariant consists of an optional clocking block, and a comma-seperated list of invariant expressions.

```SystemVerilog
invariant foobar;
    @(posedge clock)                       // Optional
    disable iff (reset)                    // Optional
    X -> Y, Z;                             // Equivalent to (!X || Y) && Z
    else A, B;                             // Optional, equivalent to A && B
endinvariant
```

When a disable iff expression is given, the invariant is ignored when the expression is true.
When a clock expression is given, the invariant is checked in every step corresponding to a clock edge and an optional `else` expression is checked in cycles with no clock edge.

### Future FF System Functions

IVY adds the system functions `$future_ff`, `$rising_ff`, `$falling_ff`, `$steady_ff`, `$changing_ff`
which are defined analogous to the (unsupported) standard `$future_gclk`, etc. functions but instead of sampling on the next global clock, they take the input to the flip flop designated by
the argument signal. As such they can only be used directly on FFs and not on arbitrary expressions.

These system functions are useful to express purely combinational invariants that restrict possible
state transitions.

### Proof-Endproof Blocks

```
[automatic] proof <name>(...) [priority <int>];
    [local] assert invariant <name>(...);
    [cross] assume invariant|proof <name>(...);
    solve invariant|sequence|property <name>(...);
    solve proof <name>(...) [with "<solver>"] [priority <int>];
    solve with "<solver>" [priority <int>];
endproof

solve invariant|sequence|property <name>(...);
solve proof <name>(...) [with "<solver>"] [priority <int>];
```

A tool like IVY shall ignore all `solve with` statements it can't understand, then pick the one with the highest priority. An implementation-defined mechanism is used to pick a solver when two or more solvers with the same priority remain. (Note that the priority can be an expression using constant proof arguments.) The chosen solver is then queued with it's priority, or the priority specified in the proof header if it has one, or the priority of the calling `solve` statement if that has one. (If different solvers are being added for the same proof via different routes, the one with the higher priority remains, using an implementation-defined method to resolve a tie.)

Proofs can run in parallel arbitarily, but should be queued for execution approximately in order of descending priority. Running proofs should be terminated when all their asserted properties have benn proven independently via other proofs already.

Proofs with negative priority should only be attempted after all proofs with positive priority for the same properties have failed. The default priority is zero and proofs with negative priority can run in parallel to proofs with priority zero for the same properties.

The `"<solver>"` syntax for SBY: `"sby [options] [engine spec]"`. For example: `"sby --depth 15 smtbmc yices"`. Supported options are "--depth" and "--multiclock".

#### Assert-Assume

##### `[local] assert invariant|sequence|property <name>;`

Assert the specified properties. If the proof suceeds, the properties will be considered proven.

##### `[cross] assume invariant|sequence|property <name>;`

Assume the specified properties. They must be proven independently.

With `cross` the speciefied properties are only assumed in the prior, not the final state. This allows proofs to assume each others asserted properties.

##### `[cross] assume proof <name>;`

Assume all properties that are asserted in the specified proof, unless they are asserted with `local`.

##### `export [cross] [assert|assume] invariant|sequence|property|proof <name>;`

Export the specified property. Any proof `use`ing this one will assume the specified properties.

(Also `assert`, `assume`, or `cross assume` the specified properties as indicated.)

##### `[export] use proof <name>;`

Assume the properties exported in the other proof.

#### Proof Management

##### `[automatic] proof ... endproof`

With `automatic` the proof is added to the database automatically, when it has no arguments, or when
it's used in any other proof with `use` or `assume`. Without `automatic`, the proof is only added to
the database when added explicitly with `solve proof <name>;`.

##### `solve proof <name>;`

When elaborating this proof, also elaborate the specific proof, and add it to the database.

The `solve proof` statement can also be used in module context to specify the "top-level" proofs.

##### `solve with "<solver-command>";`

Which solver to use to prove the assertions in this proof. Multiple `solve with` clauses can be specified and a tool is free to pick wichever it supports. A tool should not attempt to solve a proof that has no "solve with" clause it supports.

A proof that doesn't assert anything doesn't need a `solve with` clause, and all `solve with` clauses provided for such a proof are ignored.


### Planned Extensions

#### Abstractions

##### `[export] disable <entity>;`

Specify a cutpoint.

If specified with `export` then any proof `use`ing this one will inherit the cutpoint.

##### `implents <entity>;`

A cut point that other proofs will inherit but this proof is not using itself.

##### `[export] inside <entity>;`

A way to cutpoint everything except the given entitny. Can be used multiple times and mixed with `disable` statements.

If any `inside` clauses are provided, then all cells are removed that are not inside any of the entities listed, or a
direct prefix of any of the entities. I.e. with `inside top.foo;`, `inside top.bar;` we will keep the top module itself,
and the hierarchies below `top.foo` and `top.bar`, but remove all other cells in `top`. Wires connecting the remaining
cells are kept.

Disable statements are executed independent of `inside` clauses.

#### Case Management

##### `assert table (<expr>)|{<expr-list>} [not] within {<const-list>};`

Prove that the const list contains at least all the possible cases (or only impossible cases) for the given expression(s).

##### `[export] [assume] table (<expr>)|{<expr-list>} [not] within {<const-list>};`

Restrict this proof to a certain case or list of cases. (The condition is only assumed in the last cycle of the witness, i.e. the cycle in which the property would fail.) IVY will keep track of the cases and make sure that a property is either proven for all cases, or is only used in cases with compatible restrictions. (Either `export` or `assume` or both must be present for the statement to be valid.)
