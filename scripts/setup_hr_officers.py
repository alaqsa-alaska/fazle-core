#!/usr/bin/env python3
"""
Setup script for HR Officer users (OfficeAssistant01, OfficeAssistant02).
Creates admin users with 'operator' role for payroll data entry on mobile.

Run with: python3 scripts/setup_hr_officers.py
"""
import asyncio
import sys
import os

# Add core to path so we can import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.rbac import add_admin, set_role, issue_api_key
from app.database import init_db, close_db


async def setup_hr_officers():
    """Create HR Officer users in RBAC system."""
    await init_db()
    
    try:
        # HR Officer 1
        print("Creating OfficeAssistant01...")
        result1 = await add_admin(
            phone="01700000001",  # Placeholder phone for non-phone-based user
            name="OfficeAssistant01",
            role="operator",
            granted_by="setup_script"
        )
        print(f"  ✓ Created: {result1}")
        
        # Generate API key for Officer 1
        key1 = await issue_api_key("01700000001")
        print(f"  ✓ API Key: {key1['api_key']}")
        
        # HR Officer 2
        print("\nCreating OfficeAssistant02...")
        result2 = await add_admin(
            phone="01700000002",  # Placeholder phone for non-phone-based user
            name="OfficeAssistant02",
            role="operator",
            granted_by="setup_script"
        )
        print(f"  ✓ Created: {result2}")
        
        # Generate API key for Officer 2
        key2 = await issue_api_key("01700000002")
        print(f"  ✓ API Key: {key2['api_key']}")
        
        print("\n" + "="*60)
        print("HR OFFICER SETUP COMPLETE")
        print("="*60)
        print("\nCredentials for mobile payroll dashboard:")
        print(f"\nUser 1:")
        print(f"  Username: OfficeAssistant01")
        print(f"  API Key: {key1['api_key']}")
        print(f"  Password: Office1234 (use API Key above instead)")
        
        print(f"\nUser 2:")
        print(f"  Username: OfficeAssistant02")
        print(f"  API Key: {key2['api_key']}")
        print(f"  Password: Assistant1234 (use API Key above instead)")
        
        print("\nAccess the payroll dashboard:")
        print("  URL: https://your-server/app/static/payroll.html")
        print("  Paste the API Key in the login field")
        
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(setup_hr_officers())
