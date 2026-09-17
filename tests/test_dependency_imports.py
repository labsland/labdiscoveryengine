"""Catch incompatible dependency resolution outside test-suite module stubs."""
import subprocess
import sys
import unittest


class DependencyImportsTest(unittest.TestCase):
    def test_mongo_and_http_dependencies_import_in_a_fresh_interpreter(self):
        result = subprocess.run(
            [sys.executable, '-c',
             'import motor.motor_asyncio; import aiohttp; '
             'assert callable(aiohttp.ClientTimeout); '
             'assert callable(aiohttp.TCPConnector)'],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
