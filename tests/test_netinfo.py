import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import netinfo


class LookupTest(unittest.TestCase):
    def test_dnr_mobile_operator(self):
        found = netinfo.lookup("85.193.64.9")
        self.assertEqual((found["asn"], found["kind"]), (203451, "mobile"))

    def test_mixed_operators_are_not_rejected(self):
        self.assertEqual(netinfo.lookup("31.133.32.5")["kind"], "mixed")
        self.assertEqual(netinfo.lookup("2.63.150.1")["kind"], "mixed")

    def test_matrix_is_a_home_provider_despite_the_name(self):
        found = netinfo.lookup("5.8.165.1")
        self.assertEqual((found["asn"], found["kind"]), (3181, "home"))

    def test_unknown_network_has_asn_but_no_kind(self):
        found = netinfo.lookup("195.128.159.1")
        self.assertIsNotNone(found["asn"])
        self.assertIsNone(found["kind"])
        self.assertTrue(found["name"].startswith("AS"))

    def test_foreign_and_bad_addresses(self):
        self.assertEqual(netinfo.lookup("8.8.8.8")["asn"], None)
        self.assertEqual(netinfo.lookup("мусор")["asn"], None)


if __name__ == "__main__":
    unittest.main()
