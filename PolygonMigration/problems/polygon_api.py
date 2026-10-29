import hashlib
import time
import random
import string
import requests
from urllib.parse import urlencode
from django.conf import settings
import json
import os
import sys
import zipfile
import tempfile
import redis
import logging
import shutil
import subprocess
import stat

logger = logging.getLogger(__name__)


class PolygonAPI:
    """
    A service class to interact with the Polygon API.
    Handles authentication and provides methods to fetch problem data,
    test cases, and manage migration to the configured storage backend
    (selected by STORAGE_PROVIDER via problems.storage.get_storage()).
    """

    API_URL = "https://polygon.codeforces.com/api/"

    def __init__(self):
        self.api_key = settings.POLYGON_API_KEY
        self.api_secret = settings.POLYGON_API_SECRET

    # ------------------------------------------------------------------
    # Polygon auth / request helpers
    # ------------------------------------------------------------------

    def _generate_api_sig(self, method_name, params):
        params['apiKey'] = self.api_key
        params['time'] = int(time.time())

        sorted_params = sorted(params.items())
        param_str = urlencode(sorted_params)

        rand_prefix = ''.join(
            random.choices(string.ascii_lowercase + string.digits, k=6)
        )

        to_hash = f"{rand_prefix}/{method_name}?{param_str}#{self.api_secret}"
        sha512_hash = hashlib.sha512(to_hash.encode('utf-8')).hexdigest()

        api_sig = f"{rand_prefix}{sha512_hash}"
        return api_sig, params['time']

    def _make_request(self, method_name, params=None, expect_json=True):
        if params is None:
            params = {}

        api_sig, request_time = self._generate_api_sig(method_name, params.copy())

        post_params = params.copy()
        post_params['apiKey'] = self.api_key
        post_params['apiSig'] = api_sig
        post_params['time'] = request_time

        try:
            response = requests.post(f"{self.API_URL}{method_name}", data=post_params)
            response.raise_for_status()

            if not expect_json:
                return response.text

            try:
                data = response.json()
                if data.get('status') == 'FAILED':
                    raise Exception(f"Polygon API Error: {data.get('comment')}")
                return data.get('result')
            except json.JSONDecodeError:
                if expect_json:
                    raise
                return response.text

        except requests.exceptions.RequestException as e:
            raise Exception(f"HTTP Request Error: {e}")

    def _make_plain_request(self, method_name, params=None):
        return self._make_request(method_name, params, expect_json=False)

    # ------------------------------------------------------------------
    # Polygon problem package / statements
    # ------------------------------------------------------------------

    def download_and_extract_package(self, problem_id, package_type='standard'):
        logger.info("Downloading package for problem %s", problem_id)
        temp_dir = tempfile.mkdtemp()

        try:
            problem_info = self.get_problem_info(problem_id)
            if not problem_info:
                raise Exception("Could not get problem info")

            packages = self._make_request(
                'problem.packages', {'problemId': problem_id}
            )
            if not packages:
                raise Exception("No packages available for this problem")

            latest_package = None
            for package in packages:
                if package.get('type') == package_type:
                    if (not latest_package
                            or package.get('revision', 0) > latest_package.get('revision', 0)):
                        latest_package = package

            if not latest_package:
                raise Exception(f"No {package_type} package found for this problem")

            package_id = latest_package['id']

            api_sig, request_time = self._generate_api_sig('problem.package', {
                'problemId': problem_id,
                'packageId': package_id,
                'type': package_type,
            })

            post_params = {
                'problemId': problem_id,
                'packageId': package_id,
                'type': package_type,
                'apiKey': self.api_key,
                'apiSig': api_sig,
                'time': request_time,
            }

            response = requests.post(f"{self.API_URL}problem.package", data=post_params)
            response.raise_for_status()

            if not response.content.startswith(b'PK'):
                try:
                    error_data = response.json()
                    if error_data.get('status') == 'FAILED':
                        raise Exception(f"Polygon API Error: {error_data.get('comment')}")
                except Exception:
                    pass
                raise Exception("Downloaded data is not a valid ZIP file")

            zip_path = os.path.join(temp_dir, f'problem_{problem_id}.zip')
            with open(zip_path, 'wb') as f:
                f.write(response.content)

            extract_dir = os.path.join(temp_dir, 'extracted')
            os.makedirs(extract_dir, exist_ok=True)

            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)

            problem_html_path = None
            for root, dirs, files in os.walk(extract_dir):
                for file in files:
                    if file == 'problem.html':
                        problem_html_path = os.path.join(root, file)
                        break
                if problem_html_path:
                    break

            if not problem_html_path:
                raise Exception("problem.html not found in the extracted package")

            with open(problem_html_path, 'r', encoding='utf-8') as f:
                problem_html_content = f.read()

            logger.info(
                "Successfully read problem.html content, length: %d characters",
                len(problem_html_content),
            )
            return problem_html_content

        except Exception as e:
            raise Exception(f"Error downloading/extracting package: {e}")
        finally:
            try:
                shutil.rmtree(temp_dir)
                logger.debug("Cleaned up temporary directory: %s", temp_dir)
            except Exception as cleanup_error:
                logger.warning(
                    "Failed to clean up temporary directory %s: %s",
                    temp_dir,
                    cleanup_error,
                )

    def get_problem_info(self, problem_id):
        logger.info("Fetching problem info for %s", problem_id)
        info = self._make_request('problem.info', {'problemId': problem_id})
        logger.info("Polygon problem.info response: %s", info)
        return info

    def get_statements(self, problem_id):
        logger.info("Fetching statements for %s", problem_id)
        return self._make_request('problem.statements', {'problemId': problem_id})

    def get_test_script(self, problem_id, testset='tests'):
        logger.info("Fetching test script for %s, testset", problem_id)
        try:
            return self._make_request('problem.script', {
                'problemId': problem_id,
                'testset': testset,
            })
        except Exception:
            return "Test script not found or could not be fetched."

    def get_test_cases(self, problem_id, testset='tests'):
        logger.info("Fetching test cases for %s, testset", problem_id)
        try:
            return self._make_request('problem.tests', {
                'problemId': problem_id,
                'testset': testset,
            })
        except Exception:
            return []

    def get_problem_files(self, problem_id):
        logger.info("Fetching problem files for %s", problem_id)
        try:
            return self._make_request('problem.files', {'problemId': problem_id})
        except Exception:
            return {}

    def get_file_content(self, problem_id, file_type, file_name):
        logger.info(
            "Fetching content of %s file %s for problem %s",
            file_type, file_name, problem_id,
        )
        try:
            return self._make_request('problem.viewFile', {
                'problemId': problem_id,
                'type': file_type,
                'name': file_name,
            })
        except Exception:
            return f"File {file_name} not found or could not be fetched."

    def get_all_test_cases(self, problem_id, testset='tests'):
        logger.info("Fetching all test cases for %s, testset", problem_id)
        tests = self._make_request('problem.tests', {
            'problemId': problem_id,
            'testset': testset,
        })
        all_cases = []

        logger.info("Found %d test cases to process", len(tests))

        for test in tests:
            test_index = test['index']
            logger.info(
                "Processing test case %d/%d (index: %s)",
                len(all_cases) + 1, len(tests), test_index,
            )

            test_case = {
                'index': test_index,
                'manual': test.get('manual', False),
                'is_sample': test.get('useInStatements', False),
                'description': test.get('description', ''),
            }

            try:
                test_case['input'] = self._make_plain_request('problem.testInput', {
                    'problemId': problem_id,
                    'testset': testset,
                    'testIndex': test_index,
                }) or ''

                test_case['output'] = self._make_plain_request('problem.testAnswer', {
                    'problemId': problem_id,
                    'testset': testset,
                    'testIndex': test_index,
                }) or ''

                logger.info(
                    "Successfully fetched test case %s - Input length: %d, Output length: %d",
                    test_index, len(test_case['input']), len(test_case['output']),
                )

            except Exception as e:
                logger.warning("Error fetching test case %s: %s", test_index, e)
                test_case['input'] = ''
                test_case['output'] = ''

            all_cases.append(test_case)

        logger.info(
            "Completed fetching all %d test cases for problem %s",
            len(all_cases), problem_id,
        )
        return all_cases

    # ------------------------------------------------------------------
    # Custom checker
    # ------------------------------------------------------------------

    def get_custom_checker_info(self, problem_id):
        logger.info("Fetching custom checker info for problem %s", problem_id)
        try:
            checker_info = self._make_request(
                'problem.checker', {'problemId': problem_id}
            )
            logger.debug("Raw checker info: %s", checker_info)
            if checker_info and not checker_info.startswith('std::'):
                result = {'name': checker_info, 'type': 'custom'}
                logger.info("Custom checker detected: %s", result)
                return result
            logger.info(
                "No custom checker found, using standard checker: %s", checker_info
            )
            return None
        except Exception as e:
            logger.error("Error fetching custom checker info: %s", e)
            return None

    def fetch_custom_checker_file(self, problem_id, checker_name):
        logger.info(
            "Fetching custom checker file %s for problem %s",
            checker_name, problem_id,
        )
        try:
            content = self._make_plain_request('problem.viewFile', {
                'problemId': problem_id,
                'type': 'source',
                'name': checker_name,
            })
            logger.info(
                "Fetched custom checker source, length=%d",
                len(content) if content else 0,
            )
            return content
        except Exception as e:
            logger.error("Error fetching checker as source: %s", e)

            try:
                content = self._make_plain_request('problem.viewFile', {
                    'problemId': problem_id,
                    'type': 'resource',
                    'name': checker_name,
                })
                logger.info(
                    "Fetched checker as resource, length=%d",
                    len(content) if content else 0,
                )
                return content
            except Exception as e2:
                logger.error("Error fetching checker as resource: %s", e2)

                if not checker_name.endswith('.cpp'):
                    try:
                        content = self._make_plain_request('problem.viewFile', {
                            'problemId': problem_id,
                            'type': 'source',
                            'name': checker_name + '.cpp',
                        })
                        logger.info(
                            "Fetched checker with .cpp extension, length=%d",
                            len(content) if content else 0,
                        )
                        return content
                    except Exception as e3:
                        logger.error(
                            "Error fetching checker with .cpp extension: %s", e3
                        )

                return None

    def compile_custom_checker(self, source_code, temp_dir):
        checker_dir = settings.CUSTOM_CHECKER_DIR
        if checker_dir:
            if not os.path.exists(checker_dir):
                os.makedirs(checker_dir, exist_ok=True)
            temp_dir = checker_dir
        logger.info("Compiling custom checker in %s", temp_dir)

        gpp_path = shutil.which('g++')
        if not gpp_path:
            logger.error(
                "g++ compiler not found in system PATH. "
                "Please install MinGW or another C++ compiler."
            )
            return None

        logger.info("Found g++ at: %s", gpp_path)

        source_file = os.path.join(temp_dir, 'custom_checker.cpp')
        with open(source_file, 'w', encoding='utf-8') as f:
            f.write(source_code)

        if sys.platform.startswith('win'):
            binary_filename = 'custom_checker.exe'
        else:
            binary_filename = 'custom_checker'

        binary_file = os.path.join(temp_dir, binary_filename)
        try:
            result = subprocess.run(
                [gpp_path, '-std=gnu++17', '-O2', '-o', binary_file, source_file],
                capture_output=True, text=True, cwd=temp_dir, timeout=30,
            )

            logger.info("Compilation completed with return code: %d", result.returncode)
            if result.stdout:
                logger.debug("Compilation stdout: %s", result.stdout)
            if result.stderr:
                logger.debug("Compilation stderr: %s", result.stderr)

            if result.returncode == 0:
                if os.path.exists(binary_file):
                    os.chmod(binary_file, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)
                    logger.info("Custom checker compiled successfully")
                    return binary_file
                logger.error("Compilation succeeded but binary file was not created")
                return None
            else:
                logger.error("Compilation failed with return code %d", result.returncode)
                logger.error("stdout: %s", result.stdout)
                logger.error("stderr: %s", result.stderr)
                return None
        except subprocess.TimeoutExpired:
            logger.error("Compilation timed out")
            return None
        except FileNotFoundError as e:
            logger.error("g++ compiler not found: %s", e)
            return None
        except Exception as e:
            logger.error("Error during compilation: %s", e)
            return None

    def upload_custom_checker_to_azure(
        self,
        problem_id,
        azure_account_url=None,
        azure_tenant_id=None,
        azure_client_id=None,
        azure_username=None,
        azure_password=None,
        container_name=None,
        db_problem_id=None,
    ):
        """
        Fetch, compile (if g++ available), and upload the custom checker for
        `problem_id` via the configured storage backend.

        If compilation fails, the source code is uploaded as
        custom_checker.cpp so the checker is not lost.

        Legacy azure_* arguments are retained only for backward compatibility
        with views.py and are ignored — credentials are read from Django
        settings inside the storage backend.
        """
        from problems.storage import get_storage

        logger.info("Processing custom checker for problem %s", problem_id)

        checker_info = self.get_custom_checker_info(problem_id)
        if not checker_info:
            logger.info("No custom checker found for problem %s", problem_id)
            return

        source_code = self.fetch_custom_checker_file(
            problem_id, checker_info['name']
        )
        if not source_code:
            logger.error("Failed to fetch custom checker source code")
            return

        checker_dir = settings.CUSTOM_CHECKER_DIR
        if checker_dir:
            if not os.path.exists(checker_dir):
                os.makedirs(checker_dir, exist_ok=True)
            compile_dir = checker_dir
        else:
            compile_dir = tempfile.mkdtemp()

        binary_path = self.compile_custom_checker(source_code, compile_dir)

        storage = get_storage()
        problem_id_for_naming = (
            db_problem_id if db_problem_id is not None else problem_id
        )

        if not binary_path:
            logger.warning(
                "Failed to compile custom checker, uploading source as fallback"
            )
            storage.upload_checker(
                problem_id_for_naming,
                source_code.encode('utf-8'),
                "custom_checker.cpp",
            )
            return

        logger.info("Custom checker compiled successfully at: %s", binary_path)

        try:
            with open(binary_path, 'rb') as f:
                binary_data = f.read()
        except Exception as e:
            logger.error("Error reading compiled binary: %s", e)
            return

        blob_filename = (
            'custom_checker.exe' if sys.platform.startswith('win')
            else 'custom_checker'
        )
        storage.upload_checker(problem_id_for_naming, binary_data, blob_filename)
        logger.info(
            "Uploaded custom checker for problem %s as %s",
            problem_id_for_naming, blob_filename,
        )

    # ------------------------------------------------------------------
    # Storage migration
    # ------------------------------------------------------------------

    def migrate_to_azure_blob(
        self,
        problem_id,
        azure_account_url=None,
        azure_tenant_id=None,
        azure_client_id=None,
        azure_username=None,
        azure_password=None,
        container_name=None,
        db_problem_id=None,
        testset="tests",
    ):
        """
        Migrate test cases from Redis (or Polygon fallback) to the configured
        storage backend (selected via STORAGE_PROVIDER).

        Legacy azure_* args retained for backward compatibility with views.py
        and are ignored.
        """
        from problems.storage import get_storage

        logger.info("Starting storage migration for problem %s", problem_id)

        test_cases = self.get_test_cases_from_redis(problem_id)
        if test_cases is None:
            logger.warning("Test cases not found in Redis, fetching from Polygon")
            test_cases = self.get_all_test_cases(problem_id, testset)
            self.store_test_cases_in_redis(
                problem_id, test_cases, expiry_hours=0.5
            )
        else:
            logger.info("Retrieved test cases from Redis for storage migration")

        logger.info("Retrieved %d test cases", len(test_cases))

        storage = get_storage()
        problem_id_for_naming = (
            db_problem_id if db_problem_id is not None else problem_id
        )

        logger.info("Emptying existing storage for problem %s", problem_id_for_naming)
        storage.empty_problem(problem_id_for_naming)

        uploaded_count = 0
        for idx, test in enumerate(test_cases, start=1):
            input_data = test.get("input", "")
            output_data = test.get("output", "")
            if input_data and output_data:
                storage.upload_test_case(
                    problem_id_for_naming, idx, input_data, output_data
                )
                uploaded_count += 1
            else:
                logger.warning("Skipping test #%d: missing input or output", idx)

        logger.info("Uploaded %d test cases to storage", uploaded_count)

        self.upload_custom_checker_to_azure(
            problem_id, db_problem_id=problem_id_for_naming
        )
        logger.info("Completed storage migration for problem %s", problem_id)

    def delete_azure_blob(self, db_problem_id):
        """
        Rollback helper: delete all stored blobs for a problem.

        Name retained for backward compatibility with views.py, but the
        operation is provider-agnostic (routes through StorageBackend).
        Never raises — rollback failures must not mask the original error.
        """
        from problems.storage import get_storage

        try:
            get_storage().delete_problem(db_problem_id)
            logger.info(
                "Rollback: deleted stored blobs for problem %s", db_problem_id
            )
        except Exception as e:
            logger.error(
                "Rollback delete failed for problem %s: %s", db_problem_id, e
            )

    # ------------------------------------------------------------------
    # Redis test-case cache
    # ------------------------------------------------------------------

    def delete_problem_test_case_cache(self, db_problem_id):
        pattern = f"oj_dev_with_redis_storage_test_cases_{db_problem_id}*"
        logger.info("pattern %s", pattern)
        try:
            r = redis.StrictRedis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                password=settings.REDIS_PASSWORD,
                ssl=settings.REDIS_SSL,
                ssl_cert_reqs=None,
            )
            for key in r.scan_iter(pattern):
                logger.info("deleting key %s", key)
                r.delete(key)
        except Exception as e:
            logger.error(
                "Error deleting cache keys for pattern %s: %s", pattern, e
            )

    def store_test_cases_in_redis(self, polygon_id, test_cases, expiry_hours=0.5):
        try:
            r = redis.StrictRedis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                password=settings.REDIS_PASSWORD,
                ssl=settings.REDIS_SSL,
                ssl_cert_reqs=None,
            )

            prefix = f"polygon_migration_test_cases_{polygon_id}"

            count_key = f"{prefix}_count"
            r.setex(count_key, int(expiry_hours * 3600), len(test_cases))

            for idx, test_case in enumerate(test_cases, start=1):
                test_case_key = f"{prefix}_test_{idx}"
                test_case_data = {
                    'index': test_case.get('index', idx),
                    'input': test_case.get('input', ''),
                    'output': test_case.get('output', ''),
                    'description': test_case.get('description', ''),
                    'is_sample': test_case.get('is_sample', False),
                }
                r.setex(
                    test_case_key,
                    int(expiry_hours * 3600),
                    json.dumps(test_case_data),
                )

            logger.info(
                "Stored %d test cases in Redis for polygon_id %s",
                len(test_cases), polygon_id,
            )
        except Exception as e:
            logger.error(
                "Error storing test cases in Redis for polygon_id %s: %s",
                polygon_id, e,
            )

    def get_test_cases_from_redis(self, polygon_id):
        try:
            r = redis.StrictRedis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                password=settings.REDIS_PASSWORD,
                ssl=settings.REDIS_SSL,
                ssl_cert_reqs=None,
            )

            prefix = f"polygon_migration_test_cases_{polygon_id}"
            count_key = f"{prefix}_count"
            count = r.get(count_key)

            if count is None:
                logger.info(
                    "No test cases found in Redis for polygon_id %s", polygon_id
                )
                return None

            count = int(count)
            test_cases = []

            for idx in range(1, count + 1):
                test_case_key = f"{prefix}_test_{idx}"
                test_case_data = r.get(test_case_key)

                if test_case_data:
                    test_cases.append(json.loads(test_case_data))
                else:
                    logger.warning(
                        "Missing test case %d in Redis for polygon_id %s",
                        idx, polygon_id,
                    )

            logger.info(
                "Retrieved %d test cases from Redis for polygon_id %s",
                len(test_cases), polygon_id,
            )
            return test_cases
        except Exception as e:
            logger.error(
                "Error retrieving test cases from Redis for polygon_id %s: %s",
                polygon_id, e,
            )
            return None

    def clear_test_cases_from_redis(self, polygon_id):
        try:
            r = redis.StrictRedis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                password=settings.REDIS_PASSWORD,
                ssl=settings.REDIS_SSL,
                ssl_cert_reqs=None,
            )

            prefix = f"polygon_migration_test_cases_{polygon_id}"
            pattern = f"{prefix}*"

            deleted_count = 0
            for key in r.scan_iter(pattern):
                r.delete(key)
                deleted_count += 1

            logger.info(
                "Cleared %d test case keys from Redis for polygon_id %s",
                deleted_count, polygon_id,
            )
        except Exception as e:
            logger.error(
                "Error clearing test cases from Redis for polygon_id %s: %s",
                polygon_id, e,
            )