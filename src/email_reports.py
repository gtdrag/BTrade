"""
Email Reports - Send simulation and strategy reports via email.

Uses Gmail SMTP for simplicity. Requires environment variables:
- SMTP_USER: Gmail address
- SMTP_PASSWORD: Gmail app password
- REPORT_EMAIL: Recipient email address
"""

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .historical_simulator import SimulationResult

logger = logging.getLogger(__name__)

# Gmail SMTP settings
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def get_email_config() -> dict:
    """Get email configuration from environment."""
    return {
        "smtp_user": os.environ.get("SMTP_USER"),
        "smtp_password": os.environ.get("SMTP_PASSWORD"),
        "report_email": os.environ.get("REPORT_EMAIL"),
    }


def is_email_configured() -> bool:
    """Check if email is properly configured."""
    config = get_email_config()
    return all([config["smtp_user"], config["smtp_password"], config["report_email"]])


def send_email(subject: str, html_body: str, text_body: str = None) -> bool:
    """
    Send an email using Gmail SMTP.

    Args:
        subject: Email subject
        html_body: HTML content
        text_body: Plain text fallback (optional)

    Returns:
        True if sent successfully
    """
    config = get_email_config()

    if not is_email_configured():
        logger.error("Email not configured. Set SMTP_USER, SMTP_PASSWORD, REPORT_EMAIL")
        return False

    try:
        logger.info(
            f"Attempting to send email to {config['report_email']} from {config['smtp_user']}"
        )

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = config["smtp_user"]
        msg["To"] = config["report_email"]

        # Add plain text version if provided
        if text_body:
            msg.attach(MIMEText(text_body, "plain"))

        # Add HTML version
        msg.attach(MIMEText(html_body, "html"))

        # Connect and send
        logger.info(f"Connecting to {SMTP_HOST}:{SMTP_PORT}...")
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            logger.info("TLS started, logging in...")
            server.login(config["smtp_user"], config["smtp_password"])
            logger.info("Login successful, sending email...")
            server.sendmail(
                config["smtp_user"],
                config["report_email"],
                msg.as_string(),
            )

        logger.info(f"Email sent successfully to {config['report_email']}")
        return True

    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"SMTP Authentication failed: {e}. Check SMTP_USER and SMTP_PASSWORD.")
        return False
    except smtplib.SMTPException as e:
        logger.error(f"SMTP error: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to send email: {type(e).__name__}: {e}")
        return False


def format_simulation_email(result: "SimulationResult") -> tuple:
    """
    Format a simulation result as an HTML email.

    Returns:
        Tuple of (subject, html_body, text_body)
    """
    # Subject line
    period_str = f"{result.start_date.strftime('%b %Y')} - {result.end_date.strftime('%b %Y')}"
    diff = result.evolved_performance - result.static_performance
    diff_str = f"+{diff:.1f}%" if diff > 0 else f"{diff:.1f}%"
    subject = f"BTrade Simulation Report: {period_str} ({diff_str})"

    # Build HTML
    html = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        .container {{
            background: white;
            border-radius: 8px;
            padding: 30px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        h1 {{
            color: #1a1a2e;
            border-bottom: 3px solid #d4a847;
            padding-bottom: 10px;
        }}
        h2 {{
            color: #16213e;
            margin-top: 30px;
            border-left: 4px solid #d4a847;
            padding-left: 12px;
        }}
        .summary-box {{
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: white;
            padding: 20px;
            border-radius: 8px;
            margin: 20px 0;
        }}
        .summary-box h3 {{
            color: #d4a847;
            margin-top: 0;
        }}
        .stat {{
            display: inline-block;
            margin-right: 30px;
            text-align: center;
        }}
        .stat-value {{
            font-size: 28px;
            font-weight: bold;
            color: #d4a847;
        }}
        .stat-label {{
            font-size: 12px;
            opacity: 0.8;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 15px 0;
        }}
        th, td {{
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #eee;
        }}
        th {{
            background: #f8f9fa;
            font-weight: 600;
        }}
        .positive {{ color: #28a745; }}
        .negative {{ color: #dc3545; }}
        .neutral {{ color: #6c757d; }}
        .review-card {{
            background: #f8f9fa;
            border-radius: 6px;
            padding: 15px;
            margin: 10px 0;
            border-left: 4px solid #d4a847;
        }}
        .review-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
        }}
        .review-date {{
            font-weight: bold;
            color: #1a1a2e;
        }}
        .regime {{
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 500;
        }}
        .regime-bull {{ background: #d4edda; color: #155724; }}
        .regime-bear {{ background: #f8d7da; color: #721c24; }}
        .regime-neutral {{ background: #e2e3e5; color: #383d41; }}
        .change {{
            background: #fff3cd;
            padding: 8px 12px;
            border-radius: 4px;
            margin: 5px 0;
        }}
        .reason {{
            color: #666;
            font-style: italic;
            font-size: 14px;
            margin-left: 20px;
        }}
        .footer {{
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #eee;
            color: #666;
            font-size: 12px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 Historical Simulation Report</h1>

        <div class="summary-box">
            <h3>Performance Summary</h3>
            <div class="stat">
                <div class="stat-value">{result.static_performance:+.1f}%</div>
                <div class="stat-label">Static Params</div>
            </div>
            <div class="stat">
                <div class="stat-value">{result.evolved_performance:+.1f}%</div>
                <div class="stat-label">Evolved Params</div>
            </div>
            <div class="stat">
                <div class="stat-value {'positive' if diff > 0 else 'negative' if diff < 0 else 'neutral'}">{diff_str}</div>
                <div class="stat-label">Difference</div>
            </div>
        </div>

        <h2>Simulation Details</h2>
        <table>
            <tr><th>Period</th><td>{result.start_date.strftime('%Y-%m-%d')} to {result.end_date.strftime('%Y-%m-%d')}</td></tr>
            <tr><th>Reviews Run</th><td>{len(result.reviews)}</td></tr>
            <tr><th>Parameter Changes</th><td>{result.param_changes_count()}</td></tr>
            <tr><th>API Calls</th><td>{result.total_api_calls}</td></tr>
            <tr><th>Estimated Cost</th><td>${result.estimated_cost:.2f}</td></tr>
        </table>

        <h2>Parameter Evolution</h2>
        <table>
            <tr>
                <th>Parameter</th>
                <th>Initial</th>
                <th>Final</th>
                <th>Change</th>
            </tr>
"""

    # Add parameter rows
    for param in result.initial_params:
        initial = result.initial_params[param]
        final = result.final_params[param]
        change = final - initial
        change_class = "positive" if change > 0 else "negative" if change < 0 else "neutral"
        change_str = f"{change:+.1f}" if change != 0 else "—"
        html += f"""
            <tr>
                <td><code>{param}</code></td>
                <td>{initial}</td>
                <td>{final}</td>
                <td class="{change_class}">{change_str}</td>
            </tr>
"""

    html += """
        </table>

        <h2>Review Log</h2>
"""

    # Add each review
    if not result.reviews:
        html += """
        <div class="review-card">
            <p>No reviews were executed. This could mean:</p>
            <ul>
                <li>No market data available for this period</li>
                <li>IBIT ETF launched January 2024 - earlier dates won't work</li>
                <li>Simulation period was too short</li>
            </ul>
        </div>
"""
    else:
        for review in result.reviews:
            regime_class = (
                "regime-bull"
                if "bull" in review.market_regime
                else "regime-bear"
                if "bear" in review.market_regime
                else "regime-neutral"
            )
            regime_emoji = {
                "strong_bull": "🚀",
                "bull": "📈",
                "neutral": "➡️",
                "bear": "📉",
                "strong_bear": "💥",
            }.get(review.market_regime, "❓")

            html += f"""
        <div class="review-card">
            <div class="review-header">
                <span class="review-date">Review #{review.review_number} — {review.review_date.strftime('%B %d, %Y')}</span>
                <span class="regime {regime_class}">{regime_emoji} {review.market_regime.replace('_', ' ').title()}</span>
            </div>
            <div>Backtest Return: <strong>{review.backtest_return:+.1f}%</strong></div>
"""

            if review.recommendations:
                for rec in review.recommendations:
                    param = rec.get("parameter", "?")
                    old = rec.get("old_value", "?")
                    new = rec.get("new_value", "?")
                    conf = rec.get("confidence", "?")
                    reason = rec.get("reason", "")
                    html += f"""
            <div class="change">
                <strong>{param}</strong>: {old} → {new} <span class="neutral">[{conf} confidence]</span>
            </div>
            <div class="reason">{reason}</div>
"""
            else:
                html += """
            <div style="color: #666;">No parameter changes recommended</div>
"""

            if review.watch_items:
                html += "<div style='margin-top: 10px;'><strong>Watch Items:</strong></div>"
                for item in review.watch_items:
                    cat = item.get("category", "?")
                    desc = item.get("description", "?")
                    html += f"""
            <div style="margin-left: 15px; color: #856404;">⚠️ [{cat}] {desc}</div>
"""

            html += """
        </div>
"""

    # Footer
    html += f"""
        <div class="footer">
            <p>This report was generated by BTrade's Historical Simulation feature.</p>
            <p>The simulation ran {len(result.reviews)} AI-powered strategy reviews, testing parameter adjustments against historical data.</p>
            <p><strong>Disclaimer:</strong> Past performance does not guarantee future results. This is for educational purposes only.</p>
        </div>
    </div>
</body>
</html>
"""

    # Plain text version
    text = f"""
BTrade Historical Simulation Report
====================================

Period: {result.start_date.strftime('%Y-%m-%d')} to {result.end_date.strftime('%Y-%m-%d')}

PERFORMANCE SUMMARY
-------------------
Static Params:  {result.static_performance:+.1f}%
Evolved Params: {result.evolved_performance:+.1f}%
Difference:     {diff_str}

SIMULATION DETAILS
------------------
Reviews Run: {len(result.reviews)}
Parameter Changes: {result.param_changes_count()}
API Calls: {result.total_api_calls}
Estimated Cost: ${result.estimated_cost:.2f}

PARAMETER EVOLUTION
-------------------
"""
    for param in result.initial_params:
        initial = result.initial_params[param]
        final = result.final_params[param]
        text += f"{param}: {initial} → {final}\n"

    text += """
REVIEW LOG
----------
"""
    for review in result.reviews:
        text += f"\nReview #{review.review_number} — {review.review_date.strftime('%Y-%m-%d')}\n"
        text += f"  Regime: {review.market_regime}\n"
        text += f"  Return: {review.backtest_return:+.1f}%\n"
        if review.recommendations:
            for rec in review.recommendations:
                text += f"  Change: {rec.get('parameter')} {rec.get('old_value')} → {rec.get('new_value')}\n"
                text += f"    Reason: {rec.get('reason', 'N/A')}\n"
        else:
            text += "  No changes\n"

    text += """
---
Disclaimer: Past performance does not guarantee future results.
This is for educational purposes only.
"""

    return subject, html, text


def send_simulation_report(result: "SimulationResult") -> bool:
    """
    Send a simulation result as an email report.

    Args:
        result: The simulation result to send

    Returns:
        True if sent successfully
    """
    subject, html, text = format_simulation_email(result)
    return send_email(subject, html, text)
