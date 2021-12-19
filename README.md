# InVariants with Yosys (IVY)

Mockup IVY file
```SystemVerilog
invariant myIV_1(x, y);
  let s = x.foo + y.bar;
  assert (s < 3);
endinvariant

invariant myIV_2(x, y);
  ...
endinvariant

invariant myIV_3(x, y);
  ...
endinvariant

invariant myIV(a, b, c);
  refine myIV_1(a, b) as my1;
  refine myIV_2(a, c) as my2;
  refine myIV_3(b, c) as my3;
  inductive (5) with myAxiom_nostall(a);
  inductive (20) with myBB_fifo(a, 5);
endinvarant

abstraction myAbs_fifo_blackbox(x, n);
  refine myIV_stall_timeout(a, n);
  cutpoint x.fifo.memory;
endabstraction

axiom myAxiom_nostall(x);
  assert (!x.stall);
endaxiom

invariant myIV_stall_timeout(x, n);
  assert (x.fifo.stallTimeout <= n);
  inductive (50) at x.fifo;
endinvariant
```
