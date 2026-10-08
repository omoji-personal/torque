"""Start the Salesforce CLI through the shared launcher (jsc_common.tools), which
finds it where Windows installs it as a batch file. With only this package on
sys.path the plain subprocess call is used, as before."""
import subprocess

try:
    from jsc_common.tools import run
except ImportError:
    run = subprocess.run
