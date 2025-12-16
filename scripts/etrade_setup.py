#!/usr/bin/env python3
"""
E*TRADE API Setup Script.

This script helps you:
1. Configure your E*TRADE API credentials
2. Authenticate with E*TRADE (OAuth flow)
3. Select your trading account
4. Test the connection

Prerequisites:
- E*TRADE account with API access enabled
- Consumer Key and Consumer Secret from E*TRADE Developer Portal
  (https://developer.etrade.com/getting-started)
"""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.etrade_client import ETradeClient  # noqa: E402


def get_credentials():
    """Get E*TRADE credentials from user or environment."""
    print("\n" + "=" * 60)
    print("E*TRADE API Setup")
    print("=" * 60)

    # Check environment variables first
    consumer_key = os.environ.get("ETRADE_CONSUMER_KEY", "")
    consumer_secret = os.environ.get("ETRADE_CONSUMER_SECRET", "")

    if consumer_key and consumer_secret:
        print("\nFound credentials in environment variables.")
        use_env = input("Use these credentials? [Y/n]: ").strip().lower()
        if use_env != "n":
            return consumer_key, consumer_secret

    print("\nEnter your E*TRADE API credentials:")
    print("(Get them from https://developer.etrade.com/getting-started)")
    print()

    consumer_key = input("Consumer Key: ").strip()
    consumer_secret = input("Consumer Secret: ").strip()

    if not consumer_key or not consumer_secret:
        print("\nError: Both Consumer Key and Consumer Secret are required.")
        sys.exit(1)

    # Offer to save to environment
    print("\n" + "-" * 40)
    print("To save credentials permanently, add these to your ~/.zshrc or ~/.bashrc:")
    print(f'  export ETRADE_CONSUMER_KEY="{consumer_key}"')
    print(f'  export ETRADE_CONSUMER_SECRET="{consumer_secret}"')
    print("-" * 40)

    return consumer_key, consumer_secret


def authenticate(consumer_key: str, consumer_secret: str, sandbox: bool = False):
    """Authenticate with E*TRADE."""
    print("\n" + "=" * 60)
    print("Step 2: OAuth Authentication")
    print("=" * 60)

    env = "SANDBOX" if sandbox else "PRODUCTION"
    print(f"\nConnecting to E*TRADE {env} environment...")

    client = ETradeClient(
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
        sandbox=sandbox,
    )

    # Check if we have saved tokens
    if client.is_authenticated():
        print("\nFound saved OAuth tokens. Testing connection...")
        try:
            accounts = client.list_accounts()
            print(f"Success! Found {len(accounts)} account(s).")
            return client
        except Exception as e:
            print(f"Saved tokens expired. Re-authenticating... ({e})")

    # Perform OAuth flow
    success = client.authenticate()
    if not success:
        print("\nAuthentication failed!")
        sys.exit(1)

    return client


def select_account(client: ETradeClient):
    """Select trading account."""
    print("\n" + "=" * 60)
    print("Step 3: Select Trading Account")
    print("=" * 60)

    accounts = client.list_accounts()

    if not accounts:
        print("\nNo accounts found!")
        sys.exit(1)

    print("\nAvailable accounts:")
    print("-" * 60)

    for i, acct in enumerate(accounts, 1):
        acct_id = acct.get("accountId", "N/A")
        acct_key = acct.get("accountIdKey", "N/A")
        acct_type = acct.get("accountType", "Unknown")
        acct_desc = acct.get("accountDesc", "")

        print(f"  [{i}] {acct_id} - {acct_type}")
        print(f"      Description: {acct_desc}")
        print(f"      Account Key: {acct_key}")
        print()

    selection = input(f"Select account [1-{len(accounts)}]: ").strip()

    try:
        idx = int(selection) - 1
        if 0 <= idx < len(accounts):
            selected = accounts[idx]
            account_id_key = selected.get("accountIdKey")
            print(f"\nSelected: {selected.get('accountId')} ({selected.get('accountType')})")
            return account_id_key
    except ValueError:
        pass

    print("\nInvalid selection.")
    sys.exit(1)


def test_account(client: ETradeClient, account_id_key: str):
    """Test account access and show balance."""
    print("\n" + "=" * 60)
    print("Step 4: Test Account Access")
    print("=" * 60)

    try:
        # Get balance
        cash = client.get_cash_available(account_id_key)
        print(f"\nCash available for trading: ${cash:,.2f}")

        # Get positions
        positions = client.get_account_positions(account_id_key)
        if positions:
            print(f"\nCurrent positions: {len(positions)}")
            for pos in positions[:5]:  # Show first 5
                symbol = pos.get("symbolDescription", pos.get("Product", {}).get("symbol", "?"))
                qty = pos.get("quantity", 0)
                print(f"  - {symbol}: {qty} shares")
        else:
            print("\nNo open positions.")

        # Test quote
        print("\nTesting market data (IBIT quote)...")
        quote = client.get_ibit_quote()
        print(f"  IBIT Last: ${quote['last_price']:.2f}")
        print(f"  IBIT Change: {quote['change_pct']:+.2f}%")

        return True

    except Exception as e:
        print(f"\nError accessing account: {e}")
        return False


def save_setup(account_id_key: str):
    """Save setup instructions."""
    print("\n" + "=" * 60)
    print("Setup Complete!")
    print("=" * 60)

    print("\nTo use live trading, set these environment variables:")
    print("-" * 60)
    print(f'  export ETRADE_ACCOUNT_ID="{account_id_key}"')
    print()
    print("Then start the dashboard and switch to LIVE mode.")
    print()
    print("Note: OAuth tokens expire daily at midnight ET.")
    print("      The bot will automatically renew them when running.")


def main():
    """Main setup flow."""
    print("\nWelcome to the E*TRADE Trading Bot Setup!")
    print("This will guide you through connecting your E*TRADE account.")

    # Ask about sandbox mode
    print("\nWhich environment do you want to use?")
    print("  [1] Production (real money)")
    print("  [2] Sandbox (testing, no real trades)")

    env_choice = input("\nSelect environment [1/2]: ").strip()
    sandbox = env_choice == "2"

    if sandbox:
        print("\nUsing SANDBOX environment (no real trades).")
        print("Note: Sandbox has limited functionality and test data.")
    else:
        print("\nUsing PRODUCTION environment (REAL MONEY!).")
        confirm = input("Are you sure? [y/N]: ").strip().lower()
        if confirm != "y":
            print("Aborting.")
            sys.exit(0)

    # Get credentials
    consumer_key, consumer_secret = get_credentials()

    # Authenticate
    client = authenticate(consumer_key, consumer_secret, sandbox)

    # Select account
    account_id_key = select_account(client)

    # Test account
    success = test_account(client, account_id_key)

    if success:
        save_setup(account_id_key)
    else:
        print("\nSetup completed with warnings. Please verify manually.")


if __name__ == "__main__":
    main()
