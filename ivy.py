import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import yosys_ivy.main  # noqa: E402

yosys_ivy.main.main()
