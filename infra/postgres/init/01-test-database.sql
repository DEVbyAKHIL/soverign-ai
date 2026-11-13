-- Runs once, when the pgdata volume is first initialised. The test suite refuses any database
-- whose name does not end in _test (tests/conftest.py), so it can never touch the app data.
CREATE DATABASE gateway_test;
