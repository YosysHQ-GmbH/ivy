import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "src"))

import yosys_ivy  # noqa: E402

yosys_ivy.main()
