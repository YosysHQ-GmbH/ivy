# InVariants with Yosys (IVY)

See https://yosyshq.readthedocs.io/projects/ivy/en/latest/ for documentation on how to use IVY.

---

IVY is part of the [Tabby CAD Suite](https://www.yosyshq.com/tabby-cad-datasheet), which also contains all required dependencies.

* [Contact YosysHQ](https://www.yosyshq.com/contact) for a [Tabby CAD Suite](https://www.yosyshq.com/tabby-cad-datasheet) Evaluation License and download link

The IVY tooling contained in this repository is licensed under the ISC license, see [COPYING](./COPYING).
Using IVY requires the use of several other components that come with their own license terms.

While the IVY tooling is fully open source, the required language support is only available via the Yosys version included in the Tabby CAD Suite.
The tasks performed by IVY's tooling could, in principle, work without IVY's SystemVerilog extension, enabling the use with the fully open source [OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build), but currently IVY's tooling does not support any alternative interfaces that could be used to provide the same information specified via IVY's language extension.
