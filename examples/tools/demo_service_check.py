"""Synthetic offline example. Never display the credential."""
import os
import sys

if not os.environ.get('DEMO_SERVICE_TOKEN'):
    sys.exit('Missing declared key DEMO_SERVICE_TOKEN.')
print('Demo credential is configured. No service contacted.')
