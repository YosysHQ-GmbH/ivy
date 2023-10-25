Configuration File Format Reference (.ivy)
==========================================

.. default-role:: code

.. TODO link a generic detailed mau config syntax description

An IVY project configuration file consists of sections.
Each section start with a single-line section header in square brackets.


`[options]`
-----------

The options section is used to specify project wide options.
This section is required as it is used to specify the top module.
Each specified option consists of a single line that contains the option name followed by the option value, separated by whitespace.

.. list-table::
   :widths: 10 40
   :header-rows: 1

   * - Option
     - Description
   * - `top`
     - The name of the top module. (Required)
   * - `default_solver`
     - The solver to use for proof tasks that do not specify a solver to use. (Default: `sby smtbmc`)

`[read]`
--------

This required section contains a Yosys script that is used to read the design and invariant source files.
This should only contain frontend commands like `read` or `verific {-sv|-vhdl|...}` and not any passes that cause the design to be elaborated.
In particular `hierarchy`, `prep` or `verific -import` should not be part of the `[read]` section.

The Yosys script runs in the `src` subdirectory of IVY's work directory which contains copies of the specified source files (see below).


`[script]`
----------

This optional section contains a Yosys script that is used to prepare the design for formal verification after the IVY extensions to SystemVerilog are processed.
By default, the design is prepared using the `prep` pass.
When specifying a custom `[script]` section it should include the `prep` pass or an equivalent.

`[files]`
---------

This optional section contains a list of source filenames, one per line, to be copied into the `src` subdirectory before running the `[read]` script.

`[file <filename>]`
-------------------

This optional section specifies a file to create in the `src` subdirectory.
The content of the file will be the section's content as contained in the configuration file.
This section can be present multiple times with different filenames.
