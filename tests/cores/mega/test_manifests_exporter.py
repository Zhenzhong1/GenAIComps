# Copyright (C) 2024 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import unittest
import yaml
import os
import filecmp

from comps.cores.mega.manifests_exporter import build_chatqna_manifests

class TestChatQnAManifestsExporter(unittest.TestCase):
    def tearDown(self):
        file_path = "ChatQnA_E2E_manifests.yaml"

        try:
            os.remove(file_path)
            print(f"Deleted {file_path} OK")
        except FileNotFoundError:
            print(f"{file_path} Not Found")
        except OSError as e:
            print(f"Fail to delete: {e}")

    def test_manifests(self):
        build_chatqna_manifests()
 
        self.assertTrue(filecmp.cmp("ChatQnA_E2E_manifests.yaml", "ChatQnA_E2E_manifests_base.yaml"))


if __name__ == "__main__":
    unittest.main()