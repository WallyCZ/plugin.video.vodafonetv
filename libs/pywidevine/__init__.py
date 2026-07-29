# -*- coding: utf-8 -*-
"""Self-contained Widevine CDM for Kodi addons.

Implements the same flow as the `pywidevine` library (device .wvd files,
privacy-mode license challenges, license parsing) but without its
dependencies -- only pycryptodome, which Kodi already ships as
`Cryptodome`.

Typical use:

    from libs.pywidevine import Cdm, Device, PSSH

    cdm = Cdm(Device.load(wvd_path))
    cdm.set_service_certificate(service_cert_b64)
    challenge = cdm.get_license_challenge(PSSH.from_b64(pssh_b64).init_data)
    # ... POST base64(challenge) to the license server ...
    for key in cdm.parse_license(license_bytes):
        print(key.kid, key.key)
"""
from libs.pywidevine.device import Device
from libs.pywidevine.pssh import PSSH, WIDEVINE_SYSTEM_ID
from libs.pywidevine.cdm import Cdm, Key, LicenseType

__all__ = ['Cdm', 'Device', 'Key', 'LicenseType', 'PSSH', 'WIDEVINE_SYSTEM_ID']
