#!/usr/bin/env python3
"""
Diagnostic script to check environment before starting the main application.
"""

import os
import sys
import platform
import logging

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

def check_directories():
    """Check if required directories exist and have proper permissions."""
    directories = [
        ('/app', 'Application directory'),
        ('/app/steamcmd', 'SteamCMD directory'),
        (os.environ.get('STEAM_DOWNLOAD_PATH', '/data/downloads'), 'Download directory'),
        ('/app/logs', 'Logs directory')
    ]

    for directory, description in directories:
        logging.info(f"Checking {description}: {directory}")
        if not os.path.exists(directory):
            try:
                os.makedirs(directory, exist_ok=True)
                logging.info(f"Created {directory}")
            except Exception as e:
                logging.error(f"Failed to create {directory}: {str(e)}")
                return False

        # Check permissions
        try:
            test_file = os.path.join(directory, '.permission_test')
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
            logging.info(f"{description} is writable")
        except Exception as e:
            logging.error(f"{description} is not writable: {str(e)}")
            return False

    return True

def check_environment_variables():
    """Check if required environment variables are set."""
    required_vars = ['STEAM_DOWNLOAD_PATH']
    missing_vars = [var for var in required_vars if not os.environ.get(var)]

    if missing_vars:
        logging.error(f"Missing environment variables: {', '.join(missing_vars)}")
        return False

    logging.info("Environment variables are set.")
    return True

def check_dependencies():
    """Check if required system libraries are installed."""
    dependencies = [
        ('/lib/x86_64-linux-gnu/libstdc++.so.6', 'lib32gcc-s1'),
        ('/usr/lib/x86_64-linux-gnu/libcurl.so.4', 'libcurl4'),
    ]

    missing_deps = [f"{package} ({path})" for path, package in dependencies if not os.path.exists(path)]

    if missing_deps:
        logging.error(f"Missing system dependencies: {', '.join(missing_deps)}")
        return False

    logging.info("All required system dependencies are installed.")
    return True

def check_python_modules():
    """Check if required Python modules are installed."""
    required_modules = ['gradio', 'requests', 'psutil', 'bs4', 'lxml']
    missing_modules = [module for module in required_modules if not _is_module_installed(module)]

    if missing_modules:
        logging.error(f"Missing Python modules: {', '.join(missing_modules)}")
        return False

    logging.info("All required Python modules are installed.")
    return True

def _is_module_installed(module_name):
    """Check if a Python module is installed."""
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False

def main():
    """Run all checks and report status."""
    logging.info(f"Running initialization checks on {platform.platform()}")
    
    checks = [
        ("Environment variables", check_environment_variables()),
        ("Directories", check_directories()),
        ("System dependencies", check_dependencies()),
        ("Python modules", check_python_modules())
    ]

    all_passed = all(result for _, result in checks)
    
    logging.info("Check results:")
    
    for check_name, result in checks:
        status = "✅ PASSED" if result else "❌ FAILED"
        logging.info(f"{check_name}: {status}")

    if all_passed:
        logging.info("All checks passed! The application should start correctly.")
        return 0
    
    logging.error("Some checks failed. Please fix the issues before starting the application.")
    return 1

if __name__ == "__main__":
    sys.exit(main())