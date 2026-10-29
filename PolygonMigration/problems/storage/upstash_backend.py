import logging

from upstash_blob import Bucket, BlobError

from .base import StorageBackend

logger = logging.getLogger(__name__)


class UpstashStorageBackend(StorageBackend):
    """
    Upstash Blob backend (S3-compatible).

    Uses the official upstash-blob SDK. Credentials are read from the
    UPSTASH_BLOB_TOKEN environment variable via Bucket.from_env().
    Preserves the same key layout as the original Azure implementation.
    """

    def __init__(self):
        self.bucket = Bucket.from_env()
        logger.info("UpstashStorageBackend initialized.")

    def upload_test_case(self, db_problem_id, test_number, input_data, output_data):
        input_key = f"test_cases/{db_problem_id}/{test_number:02d}"
        output_key = f"test_cases/{db_problem_id}/{test_number:02d}.a"

        self.bucket.put(input_key, input_data.encode("utf-8"))
        self.bucket.put(output_key, output_data.encode("utf-8"))

    def upload_checker(self, db_problem_id, checker_bytes, filename):
        key = f"test_cases/{db_problem_id}/{filename}"
        self.bucket.put(key, checker_bytes)

    def empty_problem(self, db_problem_id):
        prefix = f"test_cases/{db_problem_id}/"
        try:
            self.bucket.delete(prefix=prefix)
            logger.info("Emptied Upstash prefix: %s", prefix)
        except BlobError as e:
            if e.code == "invalid_input":
                logger.warning("Empty prefix or no objects for %s", prefix)
            else:
                logger.error("Failed to empty Upstash prefix %s: %s", prefix, e)
                raise

    def delete_problem(self, db_problem_id):
        self.empty_problem(db_problem_id)