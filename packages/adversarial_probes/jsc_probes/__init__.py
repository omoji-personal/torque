"""jsc-probes — adversarial probe synthesis for Salesforce code.

Adopted into JusticeserverClaude (JSC) 2026-05-04 from claudeblazer (Apache-2.0).
TAA Phase 5 P2-2.
"""

from jsc_probes.apex import generate_probes_for_class

__all__ = ["generate_probes_for_class"]
__version__ = "0.1.0"
