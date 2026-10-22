"""tdgov: compliance-by-design governance of Infrastructure as Code.

Reference implementation accompanying the article "Toward Sustainable
Software Engineering: A Framework for Resilient Infrastructure Delivery and
Technical Debt Prevention" (Mubarkoot, Mellaoui, Altmann, Egger).

Framework steps (Fig. 1 of the paper) and the modules that realise them:

    Step 1  design blueprint              -> blueprint/ (Terraform/HCL)
    Step 2  validate blueprint            -> tdgov.pipeline.validate, policies/
    Step 3  store in immutable repository -> tdgov.ledger, tdgov.governance
    Step 4  audit deployed state          -> tdgov.audit
    Step 5  report mismatches             -> tdgov.notify, tdgov.report
    Step 6  change request and approval   -> tdgov.governance
    Step 7  deployment pipeline           -> tdgov.pipeline.deploy
"""

__version__ = "1.0.0"
