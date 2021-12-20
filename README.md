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

Here's an example IVY file:

```SystemVerilog
invariant iv(x, y);
  assert iv1(x, y), iv2(x, y);
endinvariant

invariant iv1(x, y);
  let foo = x.f1(y);
  assert (foo < 3);
endinvariant

invariant iv2(x, y);
  assert (x.f2(y));
endinvariant

condition c1(x, y);
  when (x.f3(y));
  unless (x.f4(y));
endcondition

proof p1(x, y);
  // this translates to something like
  //   assert (c1 && iv1 && iv2 -> $future(iv1 && iv2));
  assert iv1(x, y), iv2(x, y);
  when c1(x, y);
endproof

proof p2(x, y);
  // this translates to something like
  //   assert (iv2 -> $future(iv2));
  //   assume (c1 && iv1 && iv2 -> $future(iv1 && iv2));
  assert iv2(x, y);
  with p1(x, y);
endproof

proof p3(x, y);
  // this translates to something like
  //   assert (iv1 && iv2 -> $future(iv1 && iv2));
  //   assume (iv2 -> $future(iv2));
  prove iv(x, y);
  with p2(x, y);
endproof

bind uut iv(fifo, 42);   // automatically find out which proofs to run
bind uut p3(fifo, 42);   // explicitly state what we want to prove
```

### Top-Level IVY Language Constructs

#### Bind

```
bind <target> <proof|invariant>;
```

The bind constructs binds invariants or proofs to scopes or instances in
the design under test. Ulitmately this defines what IVY is trying to prove.

#### Proof

```
proof <name>(<formal_args>);
  (<assert_stmt>|<with_stmt>|<when_unless_stmt>|<using_stmt>|<blackbox_cutpoint_stmt>)+
endproof
```

The proof construct defines a proof for some of the invariants defined in the IVY file,
under some of the conditions defined in the IVY file.

#### Invariant

```
invariant <name>(<formal_args>);
  (<let_stmt>|<assert_stmt>|<when_unless_stmt>|<using_stmt>)+
endinvariant
```

Defines an invariant of the circuit under test, either by providing SystemVerilog
expressions referring to the bound entity, or by combinining other invariants and
conditions.

#### Condition

```
condition <name>(<formal_args>);
  (<let_stmt>|<when_unless_stmt>|<using_stmt>)+
endcondition
```

Defines a condition that can be used for case-breaking, lemmas, and restricting
the domain of the proofs performed. IVY helps keeping track of which invariants
have been proven for which conditions.

#### Abstraction

```
abstraction <name>(<formal-args>);
  (<with_stmt>|<using_stmt>|<blackbox_cutpoint_stmt>)+
endabstractions
```

Defines an abstraction using one or more invariants and one or more blackbox
and/or cutpoint definitions.

### Statements

#### Let

```
let <name> = <expr>;
let <name>(<formal_args>) = <expr>;
```

Valid in `invariant..endinvariant` and `condition..endcondition`.

Decares a local formal variable, like the SystemVerilog `let` keyword.

#### Assert

```
assert arg1, arg2, ...;
```

Valid in `proof..endproof` and `invariant..endinvariant` blocks.

Arguments are SystemVerilog expressions in `(..)` and/or references to invariants.

#### With

```
with arg1, arg2, ...;
```

Valid in `proof..endproof` and `abstraction..endabstractions` blocks.

Arguments are references to invariants and/or proofs.

#### When/Unless

```
when arg1, arg2, ...;
unless arg1, arg2, ...;
```

Valid in `condition..endcondition`, `proof..endproof` and `invariant..endinvariant` blocks.

Arguments are SystemVerilog expressions in `(..)` and/or references to conditions.

#### Using

```
when arg1, arg2, ...;
unless arg1, arg2, ...;
```

Valid in `proof..endproof`, `invariant..endinvariant`, `condition..endcondition`, and `abstraction..endabstractions` blocks.

Arguments are SystemVerilog scopes and entities that should be preserved for the proof. (The final set of preserved entities
for any given proof is the union of all the relevant `using` statements.)

#### Backbox/Cutpoint

```
blackbox arg1, arg2, ...;
cutpoint arg1, arg2, ...;
```

Valid in `proof..endproof` and `abstraction..endabstractions` blocks.

Arguments for `blackbox` statements are SystemVerilog scopes and entities that should be blackboxed for the proof or abstraction.

Arguments for `cutpoint` statements are SystemVerilog variable names that shuld be cut for the proof or abstraction.
