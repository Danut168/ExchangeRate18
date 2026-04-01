#!/usr/bin/env python
# coding: utf-8

# ## Load Library

# In[4]:


import re
import os
import asyncio
import time as time_module
import random
import traceback
import requests
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, date, timedelta, time
from calendar import monthrange
from functools import reduce
from io import StringIO, BytesIO

# Third-party imports
import pandas as pd
import urllib3
import xml.etree.ElementTree as ET
import nest_asyncio
from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text
from dateutil import parser as date_parser

# Web automation imports
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, ElementNotInteractableException
from selenium.webdriver.support.ui import Select
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Playwright imports
from playwright.async_api import async_playwright
from playwright.sync_api import sync_playwright

# Disable warnings and apply nest_asyncio
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
nest_asyncio.apply()


# ## Date Adjustment

# Handle <br>
# If exdate is Sat/Sun, shift to Fri/Mon. Accepts str, datetime, or date.

# In[5]:


def adjust_sun_sat_exdate(exdate):
    """If exdate is Sat/Sun, shift to Fri/Mon. Accepts str, datetime, or date."""
    if exdate is None:
        exdate = date.today()
    elif isinstance(exdate, str):
        exdate = datetime.strptime(exdate, "%Y-%m-%d").date()
    elif isinstance(exdate, datetime):
        exdate = exdate.date()

    weekday = exdate.weekday()
    if weekday == 5:      # Saturday → Friday
        exdate -= timedelta(days=1)
    elif weekday == 6:    # Sunday → Monday
        exdate += timedelta(days=1)
    return exdate


# # Convert day

# In[6]:


def adjust_exdate(exdate):
    if exdate is None:
        exdate = date.today()
    elif isinstance(exdate, str):
        exdate = datetime.strptime(exdate, "%Y-%m-%d").date()
    elif isinstance(exdate, datetime):
        exdate = exdate.date()
    return exdate


# In[7]:


def normalize_date(exdate):
    if exdate is None:
        return date.today()

    if isinstance(exdate, date):
        return exdate

    if isinstance(exdate, str):
        return datetime.strptime(exdate, "%Y-%m-%d").date()

    if isinstance(exdate, datetime):
        return exdate.date()

    raise ValueError(f"Unsupported type: {type(exdate)}")


# ## Holiday Date handle
# If the day we want data is the holiday of that country, we will take the data before that.

# In[8]:


def get_last_available_date(exdate, check_func):
    """
    exdate: string 'YYYY-MM-DD' or date
    check_func: function that returns True if data exists for that date
    """
    exdate = adjust_sun_sat_exdate(exdate)

    # Keep going backwards until data exists
    while True:
        if check_func(exdate):
            return exdate
        exdate -= timedelta(days=1)


# ## Countries that take exchange rate from IMF's Source
# - Brunei
# - China
# - India
# - Japan
# - Malaysia
# - Philippines
# - Saudi Arabia
# - Singapore
# - Thailand

# In[9]:


def last_day_of_month(d: date) -> date:
    _, last_day = monthrange(d.year, d.month)
    return d.replace(day=last_day)


# In[10]:


def is_missing(v):
    """Check if value is missing/empty/NA"""
    if v is None:
        return True
    if pd.isna(v):
        return True
    s = str(v).strip()
    return s == "" or s.upper() in ["NA", "N/A", "NAN", "...", "-", "—"]


# # IMF_new
# - Brunei
# - China
# - India
# - Japan
# - Malaysia
# - Philippines
# - Saudi Arabia
# - Singapore
# - Thailand

# In[11]:


def scrap_IMF(exdate=None, max_months_back=12):
    """
    Scrape IMF exchange rate data for specified countries

    Args:
        exdate: Target date for data (string in 'YYYY-MM-DD' format, datetime, or date object)
        max_months_back: Maximum number of months to go back if data not found

    Returns:
        List of dictionaries with exchange rate data
    """
    countries_map = {
        "Brunei": "Brunei dollar",
        "China": "Chinese yuan",
        "India": "Indian rupee",
        "Japan": "Japanese yen",
        "Malaysia": "Malaysian ringgit",
        "Philippines": "Philippine peso",
        "Saudi Arabia": "Saudi Arabian riyal",
        "Singapore": "Singapore dollar",
        "Thailand": "Thai baht",
    }

    options = Options()
    options.add_argument("--headless")  # Uncomment for headless mode
    driver = webdriver.Firefox(options=options)
    results = []

    try:
        # Parse the target date
        target_date = adjust_exdate(exdate)
        print(f"🔍 Target date: {target_date.strftime('%Y-%m-%d')}")

        # Iterate back through months to find data
        for months_back in range(max_months_back + 1):
            # Calculate month and year for URL generation
            months_to_subtract = months_back
            year = target_date.year
            month = target_date.month - months_to_subtract

            # Adjust year if month goes below 1
            while month < 1:
                month += 12
                year -= 1

            # Get the last day of that month for URL
            url_date = last_day_of_month(date(year, month, 1))

            url = f"https://www.imf.org/external/np/fin/data/rms_mth.aspx?SelectDate={url_date:%Y-%m-%d}&reportType=REP"

            print(f"📅 Checking IMF data for month ending: {url_date.strftime('%B %d, %Y')}")
            driver.get(url)
            time_module.sleep(3)

# ===============================================================
            # Handle access denied retries
            for attempt in range(4):
                if "Access Denied" in driver.title or "Access Denied" in driver.page_source:
                    print(f"   ⚠️ Access Denied detected (attempt {attempt + 1}/4), waiting and retrying...")
                    driver.refresh()
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time_module.sleep(5)
                else:
                    break
# ===============================================================

            # Try to find and extract table data
            try:
                # Extract headers
                h1_elements = driver.find_elements(By.XPATH, '//*[@id="content"]/center/table[2]/tbody/tr[2]/td/div/center/table/tbody/tr[2]/th')
                h2_elements = driver.find_elements(By.XPATH, '//*[@id="content"]/center/table[2]/tbody/tr[3]/td/div/center/table/tbody/tr[2]/th')

                headers = [h.text.strip() for h in h1_elements] + [h.text.strip() for h in h2_elements][1:]

                # Extract data rows
                all_data_rows = []
                for r in range(3, 39):
                    try:
                        t1_cols = driver.find_elements(By.XPATH, f'//*[@id="content"]/center/table[2]/tbody/tr[2]/td/div/center/table/tbody/tr[{r}]/td')
                        t2_cols = driver.find_elements(By.XPATH, f'//*[@id="content"]/center/table[2]/tbody/tr[3]/td/div/center/table/tbody/tr[{r}]/td')

                        if t1_cols:
                            data1 = [c.text.strip() for c in t1_cols]
                            data2 = [c.text.strip() for c in t2_cols][1:] if t2_cols else []
                            full_row = data1 + data2
                            all_data_rows.append(full_row)
                    except NoSuchElementException:
                        continue  # Skip rows that don't exist

                # Check if we got valid data
                if all_data_rows and len(headers) > 0:
                    df = pd.DataFrame(all_data_rows, columns=headers)

                    print(f"   ✅ Found table with {len(df)} rows and {len(df.columns)} columns")

                    # Parse all date headers to find available dates
                    available_dates = []
                    for idx, header in enumerate(headers):
                        if idx == 0:  # Skip "Currency" column
                            continue

                        clean_header = header.replace('\n', ' ').strip()

                        # Try to parse date from header
                        try:
                            header_date = date_parser.parse(clean_header, fuzzy=True).date()
                            available_dates.append((idx, header_date, clean_header))
                        except Exception as e:
                            continue

                    if not available_dates:
                        print(f"   ⚠️ No parseable date columns found")
                        continue

                    # Sort dates in descending order (most recent first)
                    available_dates.sort(key=lambda x: x[1], reverse=True)

                    # NEW LOGIC: For each country, try dates in order until we find valid data
                    # We'll collect data for each country separately
                    country_data = {}

                    # Process each country
                    for country, currency in countries_map.items():
                        currency_code = currency.split()[-1]

                        # Try each date from most recent to oldest
                        for idx, header_date, clean_header in available_dates:
                            # Only consider dates on or before target_date
                            if header_date > target_date:
                                continue

                            found_data = False
                            exchange_rate = None

                            # Search for this country in the rows
                            for _, row in df.iterrows():
                                country_cell = str(row.iloc[0]).lower()

                                # Check if country or currency name appears in the cell
                                if (country.lower() in country_cell or
                                    currency.lower() in country_cell):

                                    # Get exchange rate value for this date
                                    exchange_rate = row.iloc[idx]

                                    # Check if value is missing
                                    if is_missing(exchange_rate):
                                        # Try next older date
                                        break

                                    # Clean the exchange rate value
                                    try:
                                        rate_str = str(exchange_rate).strip()
                                        # Remove any non-numeric characters except decimal point
                                        rate_clean = ''.join(c for c in rate_str if c.isdigit() or c == '.' or c == '-')
                                        if rate_clean and rate_clean != '-':
                                            exchange_rate = float(rate_clean)
                                            found_data = True
                                    except (ValueError, AttributeError):
                                        # If conversion fails, try next date
                                        break

                                    if found_data:
                                        country_data[country] = {
                                            "country": country,
                                            "unit": currency_code,
                                            "value": exchange_rate,
                                            "date_of_page(origine)": header_date.strftime("%Y-%m-%d"),
                                            "date_of_page": exdate.strftime("%Y-%m-%d"),
                                            "scrape_date": target_date.strftime("%Y-%m-%d"),
                                            "Status": "Mid-point",
                                            "website": url,
                                            "Source": "IMF",
                                        }
                                        print(f"   ✓ {country}: {exchange_rate} {currency_code} on {header_date}")
                                        break

                            if found_data:
                                break  # Found data for this country, move to next country

                    # Convert country_data dict to list
                    filtered_data = list(country_data.values())

                    if filtered_data:
                        results.extend(filtered_data)

                        # Check if we found all countries
                        found_countries = set(country_data.keys())
                        if len(found_countries) == len(countries_map):
                            print(f"   ✨ Found all {len(countries_map)} countries")
                            break  # Stop searching if we found all countries
                        else:
                            missing = set(countries_map.keys()) - found_countries
                            print(f"   ⚠️ Missing {len(missing)} countries: {', '.join(missing)}")
                            print(f"   🔍 Continuing to search older months...")
                    else:
                        print(f"   ⚠️ No data found for any countries in this month")

                else:
                    print(f"   ❌ No data table found or empty table")

            except NoSuchElementException as e:
                print(f"   ❌ Table structure not found: {str(e)}")
                continue
            except Exception as e:
                print(f"   ❌ Error processing table: {str(e)}")
                continue

        print(f"\n📊 Total results found: {len(results)}")
        if results:
            # Sort results by country for consistent output
            results.sort(key=lambda x: x['country'])
            print("Collected data:")
            for result in results:
                if result['date_of_page(origine)'] == result['scrape_date']:
                    print(f"  {result['date_of_page(origine)']}: {result['country']} - {result['value']} {result['unit']}")
                else:
                    print(f"  {result['date_of_page(origine)']}: {result['country']} - {result['value']} {result['unit']} (requested: {result['scrape_date']})")

            # Show summary
            exact_matches = sum(1 for r in results if r['date_of_page(origine)'] == r['scrape_date'])
            fallbacks = len(results) - exact_matches
            print(f"\n📈 Summary: {exact_matches} exact matches, {fallbacks} fallback dates")
        else:
            print("❌ No data found for the specified date or any previous dates")

    except Exception as e:
        print(f"❌ Critical error: {str(e)}")
        import traceback
        traceback.print_exc()

    finally:
        driver.quit()

    return results


# ## HONG KONG
# ### (The Hong Kong Association of Banks)
#     exdate: 'YYYY-MM-DD', e.g. '2025-11-21'
#     Returns mid USD/HKD = (USDSelling + USDBuyingTT) / 2

# This bank only not work at sunday so data in sunday = saturday data

# In[12]:


def adjust_hongkong(exdate):
    if exdate is None:
        exdate = date.today()
    elif isinstance(exdate, str):
        exdate = datetime.strptime(exdate, "%Y-%m-%d").date()
    elif isinstance(exdate, datetime):
        exdate = exdate.date()

    weekday = exdate.weekday()
    if weekday == 6:    # Sunday → saturday
        exdate -= timedelta(days=1)
    return exdate


# In[13]:


def adjust_exdate(exdate):
    """If exdate is Sat/Sun, shift to Fri/Mon. Accepts str, datetime, or date."""
    if exdate is None:
        exdate = date.today()
    elif isinstance(exdate, str):
        exdate = datetime.strptime(exdate, "%Y-%m-%d").date()
    elif isinstance(exdate, datetime):
        exdate = exdate.date()
    return exdate


# In[14]:


def get_last_available_date(exdate, check_func):
    """
    exdate: string 'YYYY-MM-DD' or date
    check_func: function that returns True if data exists for that date
    """
    # exdate = adjust_sun_sat_exdate(exdate)
    exdate = adjust_exdate(exdate)

    # Keep going backwards until data exists
    while True:
        if check_func(exdate):
            return exdate
        exdate -= timedelta(days=1)


# In[15]:


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/123.0 Safari/537.36"
}
def scrape_hongkong(exdate: str):
    def hongkong_has_data(check_date: date):
        """Return True if HKAB has data for the given date."""
        # check_date is a date object here
        date_str = check_date.strftime("%Y-%m-%d")
        target = adjust_hongkong(date_str)  # whatever format HK API needs
        url = f"https://www.hkab.org.hk/api/member/public/getExrate/{target}"

        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            if r.status_code != 200:
                return False

            data = r.json()
            if not data:
                return False

            # Must contain valid fields
            return ("USDSelling" in data) and ("USDBuyingTT" in data)
        except Exception:
            return False

    # Step 1 — find the actual available date (returns a date object)
    real_date = get_last_available_date(exdate, hongkong_has_data)

    # Step 2 — format for API using the REAL date
    real_date_str = real_date.strftime("%Y-%m-%d")
    target = adjust_hongkong(real_date_str)
    HIST_URL = f"https://www.hkab.org.hk/api/member/public/getExrate/{target}"

    # Step 3 — fetch actual data
    r = requests.get(HIST_URL, headers=HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()

    sell = float(data["USDSelling"])
    buy  = float(data["USDBuyingTT"])
    mid  = (sell + buy) / 2.0

    # Step 4 — return full record
    return [{
        "country": "Hong Kong",
        "value": mid,
        "unit": "HKD",
        "website": "https://www.hkab.org.hk/en/rates/exchange-rates",
        # use *actual* last available date, not the requested one
        "date_of_page": exdate.strftime("%Y-%m-%d"),
        "Source": "The Hong Kong Association of Banks",
        "Status": "buy, sell",
    }]


# # Cambodia

# In[16]:


def fetch_month_data(target):
    """Fetch table data for a given month"""
    url = f"https://www.tax.gov.kh/en/exchange-rate?for_year={target.year}&for_month={target.month:02d}"
    # print(f"Fetching: {url}")

    response = requests.get(url, verify=False)
    soup = BeautifulSoup(response.content, "html.parser")

    table = soup.find("table", {"class": "table"})
    if not table:
        return []

    rows = table.find_all("tr")
    results = []

    for row in rows[1:]:
        cols = row.find_all("td")
        if len(cols) >= 3:
            try:
                row_date = datetime.strptime(cols[0].text.strip(), "%B %d, %Y").date()
            except:
                continue

            results.append({
                "date": row_date,
                "unit": cols[1].text.strip(),
                "rate": cols[2].text.strip(),
                "source": cols[3].text.strip() if len(cols) > 3 else "N/A"
            })

    return results


# In[17]:


def scrape_cambodia(exdate=None, max_back_days=5):
    target = normalize_date(exdate)

    for i in range(max_back_days):
        current_date = target - timedelta(days=i)

        # fetch data for that month
        month_data = fetch_month_data(current_date)

        # find exact match
        for row in month_data:
            if row["date"] == current_date:
                return [{
                    "country": "Cambodia",
                    "value": row["rate"],
                    "unit": "KHR",
                    "date_of_page": exdate.strftime("%Y-%m-%d"),
                    "website": f"https://www.tax.gov.kh/en/exchange-rate?for_year={current_date.year}&for_month={current_date.month:02d}",
                    "Source": row["source"],
                    "Status": "Official Rate Riel",
                }]

    raise ValueError(f"No data found within {max_back_days} days")


# ## INDONESIA
# ### (bank indonesia, bank sentral republik indonesia)

# In[18]:


def adjust_date_indonesia(exdate):
    # Parse input (assuming YYYY-MM-DD)
    dt = normalize_date(exdate)
    # Return in DD-Mon-YYYY format, e.g., 03-Feb-2026
    return dt.strftime("%d-%b-%Y")


# In[19]:


def indonesia_scrape(exdate=None, back_day=10):
    options = Options()
    options.add_argument("--headless")  # Uncomment for headless mode
    driver = webdriver.Firefox(options=options)
    wait = WebDriverWait(driver, 10)
    results = []

    try:
        # Parse the target date
        target_date = adjust_date_indonesia(exdate)
        print(f"🔍 Target date: {target_date}")

        # Get base date for calculations
        exdate = normalize_date(exdate)
        base_date = datetime.combine(exdate, datetime.min.time())

        # Iterate back through days to find data
        for days_back in range(back_day + 1):
            # Calculate date for this iteration
            current_date = base_date - timedelta(days=days_back)
            formatted_date = adjust_date_indonesia(current_date.strftime("%Y-%m-%d"))

            url = f"https://www.bi.go.id/en/statistik/informasi-kurs/transaksi-bi/Default.aspx"

            print(f"📅 Checking Indonesia data for date: {formatted_date}")
            driver.get(url)
            time_module.sleep(2)

            # Try to find and extract table data
            try:
                # Wait for the page to load
                time_module.sleep(2)

                # Method 1: Try JavaScript to set the date directly
                try:
                    driver.execute_script(
                        f"document.getElementById('ctl00_PlaceHolderMain_g_6c89d4ad_107f_437d_bd54_8fda17b556bf_ctl00_txtTanggal').value = '{formatted_date}';"
                    )
                    print(f"✅ Date set via JavaScript: {formatted_date}")
                except Exception as js_error:
                    print(f"⚠️ JavaScript date setting failed: {js_error}")

                    # Method 2: Try using the date picker instead of direct input
                    try:
                        # Click the date input field first
                        date_input = wait.until(EC.element_to_be_clickable(
                            (By.XPATH, '//*[@id="ctl00_PlaceHolderMain_g_6c89d4ad_107f_437d_bd54_8fda17b556bf_ctl00_txtTanggal"]')
                        ))

                        # Scroll into view
                        driver.execute_script("arguments[0].scrollIntoView(true);", date_input)
                        time_module.sleep(1)

                        # Clear using JavaScript
                        driver.execute_script("arguments[0].value = '';", date_input)

                        # Click and send keys
                        date_input.click()
                        time_module.sleep(0.5)

                        # Send keys slowly
                        for char in formatted_date:
                            date_input.send_keys(char)
                            time_module.sleep(0.1)

                        print(f"✅ Date set via direct input: {formatted_date}")
                    except Exception as e:
                        print(f"⚠️ Direct input also failed: {e}")
                        continue

                # Click search button
                try:
                    search_button = wait.until(EC.element_to_be_clickable(
                        (By.ID, "ctl00_PlaceHolderMain_g_6c89d4ad_107f_437d_bd54_8fda17b556bf_ctl00_btnSearch2")
                    ))
                    search_button.click()
                    print("✅ Search button clicked")
                except Exception as e:
                    # Try alternative click method
                    try:
                        driver.execute_script(
                            "document.getElementById('ctl00_PlaceHolderMain_g_6c89d4ad_107f_437d_bd54_8fda17b556bf_ctl00_btnSearch2').click();"
                        )
                        print("✅ Search button clicked via JavaScript")
                    except Exception as js_click_error:
                        print(f"⚠️ Search button click failed: {js_click_error}")
                        continue

                # Wait for results to load
                time_module.sleep(3)

                # Try to find the results table
                try:
                    # Wait for table to load
                    table = wait.until(EC.presence_of_element_located(
                        (By.XPATH, '//*[@id="ctl00_PlaceHolderMain_g_6c89d4ad_107f_437d_bd54_8fda17b556bf_ctl00_gvSearchResult2"]')
                    ))

                    # Extract table data
                    rows = table.find_elements(By.TAG_NAME, "tr")

                    if len(rows) > 1:  # If we have data rows
                        # Extract data from table
                        for row in rows[1:]:  # Skip header row
                            cols = row.find_elements(By.TAG_NAME, "td")
                            if len(cols) >= 4 and cols[0].text.strip() == "USD":
                                sell = cols[2].text.strip().replace(',', '')
                                buy = cols[3].text.strip().replace(',', '')

                                if sell and buy and sell != '0' and buy != '0':
                                    # Calculate rate (midpoint or whatever logic you need)
                                    # You might want to adjust this calculation
                                    sell_rate = float(sell)
                                    buy_rate = float(buy)
                                    rate = (sell_rate + buy_rate) / 2  # Or use sell_rate/buy_rate as in your original code

                                    data = {
                                        "country": "Indonesia",
                                        # 'date_of_page': current_date.strftime("%d-%b-%Y"),
                                        'unit': 'IDR',
                                        'Sell_Rate': sell_rate,
                                        'Buy_Rate': buy_rate,
                                        'value': rate,
                                        "date_of_page": exdate.strftime("%Y-%m-%d"),
                                        "website": url,
                                        "Source": "bank indonesia, bank sentral republik indonesia",
                                        "Status": "buy, sell"
                                    }
                                    results.append(data)
                                    print(f"✅ Found data for {formatted_date}: Rate = {rate}")
                                    break  # Found USD data, break row loop

                        if results:  # If we found data, break the days loop
                            print(f"✅ Data found for {formatted_date}, stopping search.")
                            break
                    else:
                        print(f"⚠️ No data rows found for {formatted_date}")

                except Exception as table_error:
                    print(f"⚠️ Table not found or empty for {formatted_date}: {table_error}")
                    # Check if there's a "no data" message
                    try:
                        no_data_msg = driver.find_element(By.XPATH, "//*[contains(text(), 'No data') or contains(text(), 'Tidak ada data')]")
                        print(f"📭 Confirmed: No data available for {formatted_date}")
                    except:
                        pass  # No specific message found

            except Exception as e:
                print(f"❌ Error processing {formatted_date}: {e}")
                continue

    except Exception as e:
        print(f"❌ General error: {e}")
        import traceback
        traceback.print_exc()

    finally:
        driver.quit()

    return results


# ## LAO (Bank of the lao P.D.R)
# This bank don't work on saturday and sunday
# so just use friday as saturday and monday as sunday's rate.

# In[20]:


def scrape_lao(exdate: str):
    url = "https://www.bol.gov.la/en/ExchangRate"

    def lao_has_data(check_date: date) -> bool:
        """Return True if BOL has USD data for this date."""
        query_date_str = check_date.strftime("%d-%m-%Y")

        payload = {
            "date": query_date_str,
            "search": "Search"
        }

        try:
            r = requests.post(
                url,
                headers=HEADERS,
                data=payload,
                timeout=15,
                verify=False,  # their SSL cert chain is broken
            )
            if r.status_code != 200:
                return False

            soup = BeautifulSoup(r.text, "html.parser")
            table = soup.find("table")
            if table is None:
                return False

            # Check if there's a USD row
            for tr in table.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) < 6:
                    continue
                code = tds[3].get_text(strip=True).upper()
                if code == "USD":
                    return True

            return False
        except Exception:
            return False

    # 1) Find the last date that actually has data
    real_date = get_last_available_date(exdate, lao_has_data)
    query_date_str = real_date.strftime("%d-%m-%Y")

    # 2) Fetch page for that actual date
    payload = {
        "date": query_date_str,
        "search": "Search"
    }

    r = requests.post(
        url,
        headers=HEADERS,
        data=payload,
        timeout=15,
        verify=False,
    )
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")

    # Extract displayed date from page (look for "Date: DD-MM-YYYY")
    date_text = None
    for elem in soup.find_all(string=True):
        if "Date:" in elem:
            m = re.search(r"(\d{2}-\d{2}-\d{4})", elem)
            if m:
                date_text = m.group(1)
                break
    if not date_text:
        raise RuntimeError("No 'Date: DD-MM-YYYY' found on page. The page may be empty or the date is invalid.")

    page_date = datetime.strptime(date_text, "%d-%m-%Y").date()
    print(f"✅ Lao page date: {page_date}, requested: {exdate}, effective: {real_date}")

    # Find the table
    table = soup.find("table")
    if table is None:
        raise RuntimeError("No table found on BOL exchange rate page")

    usd_buy = usd_sell = None

    def parse_lao_number(s: str) -> float:
        s = s.strip().replace(".", "").replace(",", ".")  # dot=thousands, comma=decimal
        try:
            return float(s)
        except Exception as e:
            raise ValueError(f"Cannot parse '{s}'") from e

    # Each row: No | Countries | Foreign Currencies | Currency Code | Buy Rates | Sell Rates
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue

        code = tds[3].get_text(strip=True).upper()

        if code == "USD":
            buy_str = tds[4].get_text(strip=True)
            sell_str = tds[5].get_text(strip=True)

            usd_buy = parse_lao_number(buy_str)
            usd_sell = parse_lao_number(sell_str)
            break

    if usd_buy is None or usd_sell is None:
        codes = [tds[3].get_text(strip=True) for tr in table.find_all("tr")
                 for tds in [tr.find_all("td")] if len(tds) >= 4]
        raise RuntimeError(f"USD row not found. Available currency codes: {codes}")

    mid = (usd_buy + usd_sell) / 2.0

    return [{
        "country": "Lao",
        "unit": "LAK",
        "buy": usd_buy,
        "sell": usd_sell,
        "value": mid,                           # what you asked for
        "date_of_page": exdate.strftime("%Y-%m-%d"),     # actual data date
        "website": url,
        "Source": "Bank of the lao P.D.R",
        "Status": "buy, sell"
    }]


# ## MYANMAR (Central Bank of Myanmar)
# Scrape USD reference rate (MMK per USD) from CBM's history JSON API.
# 
# Returns:
#     dict with keys: country, value, unit, date_of_page, website

# In[21]:


def scrape_myanmar(exdate=None):
    base_url = "https://forex.cbm.gov.mm/api/history"

    # If no date provided, start from "today" (UTC) as YYYY-MM-DD string
    if exdate is None:
        start_date_str = datetime.utcnow().strftime("%Y-%m-%d")
    else:
        start_date_str = exdate  # expected "YYYY-MM-DD"

    def myanmar_has_data(check_date: date) -> bool:
        """
        Returns True if CBM API has USD data for the given date.
        API format is dd-mm-yyyy.
        """
        dt_for_api = check_date.strftime("%d-%m-%Y")
        url = f"{base_url}/{dt_for_api}"

        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                return False

            data = r.json()

            # According to your comment, "no data" => [] (empty list)
            if isinstance(data, list):
                return False

            # Should be a dict with "rates" and "USD"
            rates = data.get("rates", {})
            return "USD" in rates
        except Exception:
            return False

    # 1) Find the last date that actually has data
    real_date = get_last_available_date(start_date_str, myanmar_has_data)
    dt_for_api = real_date.strftime("%d-%m-%Y")
    dt_for_page = real_date.strftime("%Y-%m-%d")

    # 2) Fetch the actual data for that date
    url = f"{base_url}/{dt_for_api}"
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()

    data = r.json()

    # Handle unexpected formats defensively
    if isinstance(data, list):
        if not data:
            raise ValueError(f"No Myanmar CBM rate data for date {dt_for_page}")
        else:
            raise ValueError(f"Unexpected JSON format from CBM API for date {dt_for_page}: list with elements")

    rates = data.get("rates", {})
    if "USD" not in rates:
        raise ValueError(f"USD rate not found in CBM response for date {dt_for_page}")

    usd_rate_str = rates["USD"]
    usd_rate = float(usd_rate_str)

    return [{
        "country": "Myanmar",
        "value": usd_rate,                        # e.g. 2100.0
        "unit": "MMK",                            # 1 USD = X MMK
        "requested_date": exdate or start_date_str,  # what you asked for
        "date_of_page": exdate.strftime("%Y-%m-%d"),              # actual data date (after holiday adjustment)
        "website": "https://forex.cbm.gov.mm/index.php/fxrate/history",
        "Source": "Central Bank of Myanmar",
        "Status": "only one"
    }]


# ## PAKISTAN (National Bank of Pakistan)

# In[22]:


def _is_number_line(s: str) -> bool:
    s = s.strip()
    if not s:
        return False
    # digits, optional commas, optional decimal part – e.g. 280.95, 1,234.50
    return bool(re.fullmatch(r"\d[\d,]*\.?\d*", s))


# Scrape USD reference rate (PKR per USD) from NBP daily rate sheet PDF.
# 
#     Heuristic:
#     - Find 'US DOLLAR' line
#     - First numeric-only line after that  -> TT Selling
#     - Second numeric-only line after that -> TT Buying

# In[23]:


def scrape_pakistan(exdate=None):
    base_url = "https://www.nbp.com.pk/RateSheetFiles"

    # --- 1. Choose starting date (requested or today) as YYYY-MM-DD string ---
    if exdate is None:
        start_date_str = datetime.utcnow().strftime("%Y-%m-%d")
    else:
        start_date_str = exdate  # expected 'YYYY-MM-DD'

    # --- 2. Define "has data" checker for a given date ---
    def pakistan_has_data(check_date: date) -> bool:
        dt_for_pdf = check_date.strftime("%d-%m-%Y")  # dd-mm-yyyy
        url = f"{base_url}/NBP-RateSheet-{dt_for_pdf}.pdf"

        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code != 200:
                return False

            pdf_text = extract_text(BytesIO(r.content))
            # Simple sanity check: file exists and mentions US DOLLAR
            return "US DOLLAR" in pdf_text.upper()
        except Exception:
            return False

    # --- 3. Find last date where the PDF exists and has USD data ---
    real_date = get_last_available_date(start_date_str, pakistan_has_data)
    dt_for_pdf  = real_date.strftime("%d-%m-%Y")   # for PDF filename
    dt_for_page = real_date.strftime("%Y-%m-%d")   # stored in Excel / output

    url = f"{base_url}/NBP-RateSheet-{dt_for_pdf}.pdf"

    # --- 4. Fetch PDF for the actual (holiday-adjusted) date ---
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()

    pdf_text = extract_text(BytesIO(r.content))
    lines = [ln.strip() for ln in pdf_text.splitlines() if ln.strip()]

    # --- 5. Find 'US DOLLAR' line index ---
    usd_idx = None
    for i, ln in enumerate(lines):
        if "US DOLLAR" in ln.upper():
            usd_idx = i
            break

    if usd_idx is None:
        raise ValueError(f"'US DOLLAR' not found in NBP PDF for {dt_for_page}")

    # --- 6. Scan forward for numeric lines and collect them ---
    numeric_vals = []
    for ln in lines[usd_idx + 1 :]:
        if _is_number_line(ln):
            val = float(ln.replace(",", ""))
            numeric_vals.append(val)
            if len(numeric_vals) > 38:
                break

    if len(numeric_vals) < 39:
        raise ValueError(
            f"Not enough numeric rates found after USD row. "
            f"Found {len(numeric_vals)} numbers: {numeric_vals}"
        )

    # Your existing logic: buy = numeric_vals[38]
    buy = numeric_vals[38]
    sell = numeric_vals[25]
    mid = (buy + sell) / 2.0

    return [{
        "country": "Pakistan",
        "value": mid,                      # update
        "unit": "PKR",                     # 1 USD = X PKR
        "date_of_page": exdate.strftime("%Y-%m-%d"),       # actual date of the PDF / rate
        "website": url,
        "Source": "National Bank of Pakistan",
        "Status": "buy",
    }]


# ## Veitnam
# ### (Ngân Hàng Nhà Nước Việt Nam)
# State Bank of Vietnam (SBV)

# In[24]:


def scrape_vietnam(exdate=None):
    """
    Vietnam – SBV reference rate.
    Uses get_last_available_date to walk backwards until a day with data is found.
    """

    # 1) Decide starting date (requested or today)
    if exdate is None:
        start_date_str = datetime.utcnow().strftime("%Y-%m-%d")
    else:
        start_date_str = exdate  # expected 'YYYY-MM-DD'

    BASE_URL = (
        "https://sbv.gov.vn/o/headless-delivery/v1.0/"
        "content-structures/3450514/structured-contents"
    )

    HEADER = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/123.0 Safari/537.36",
        "Accept": "application/json"
    }

    def vietnam_has_data(check_date: date) -> bool:
        """Return True if SBV has any USD rate item for this date."""
        d_str = check_date.strftime("%Y-%m-%d")
        end_str = (check_date + timedelta(days=1)).strftime("%Y-%m-%d")

        url = (
            f"{BASE_URL}"
            f"?filter=datePublished%20ge%20{d_str}T00%3A00%3A00.000Z"
            f"%20and%20datePublished%20le%20{end_str}T00%3A00%3A00.000Z"
        )

        try:
            r = requests.get(url, headers=HEADER, timeout=15)
            if r.status_code != 200:
                return False

            data = r.json()
            items = data.get("items", [])
            if not items:
                return False

            # Optional: check USD exists in at least one item
            for item in items:
                content_fields = item.get("contentFields", [])
                for field in content_fields:
                    if field.get("name") == "tyGiaThamKhaos" and field.get("repeatable"):
                        nested = field.get("nestedContentFields", [])
                        for sub in nested:
                            if (
                                sub.get("name") == "ngoaiTe"
                                and sub.get("contentFieldValue", {}).get("data", "").startswith("USD")
                            ):
                                return True
            return False
        except Exception:
            return False

    # 2) Find the last date with valid USD data
    real_date = get_last_available_date(start_date_str, vietnam_has_data)
    d_str = real_date.strftime("%Y-%m-%d")
    end_date = (real_date + timedelta(days=1)).strftime("%Y-%m-%d")

    url = (
        f"{BASE_URL}"
        f"?filter=datePublished%20ge%20{d_str}T00%3A00%3A00.000Z"
        f"%20and%20datePublished%20le%20{end_date}T00%3A00%3A00.000Z"
    )

    # 3) Fetch actual data for that date
    try:
        r = requests.get(url, headers=HEADER, timeout=15)
        r.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"HTTP request failed for Vietnam SBV: {e}")

    try:
        data = r.json()
    except ValueError as e:
        raise RuntimeError(f"Failed to parse JSON from SBV: {e}")

    items = data.get("items", [])
    if not items:
        raise RuntimeError(f"No exchange rate items found for Vietnam on {d_str}.")

    item = items[0]
    content_fields = item.get("contentFields", [])

    # Parse reference date & rates
    ref_date = None
    rates = []

    for field in content_fields:
        name = field.get("name")

        if name == "ngayApDung":
            val = field.get("contentFieldValue", {}).get("data")
            if val:
                try:
                    dt = date_parser.isoparse(val)
                    ref_date = dt.astimezone(timezone(timedelta(hours=7))).date()  # ICT
                except Exception:
                    pass  # fallback later

        if name == "tyGiaThamKhaos" and field.get("repeatable"):
            nested = field.get("nestedContentFields", [])
            curr = {}
            for sub in nested:
                n = sub.get("name")
                v = sub.get("contentFieldValue", {}).get("data")
                if n == "ngoaiTe" and v:
                    parts = v.split("-", 1)
                    curr["currency"] = parts[0].strip()
                    curr["name"] = parts[1].strip() if len(parts) > 1 else v
                elif n == "mua":
                    try:
                        curr["buy"] = float(v) if v else 0.0
                    except Exception:
                        curr["buy"] = 0.0
                elif n == "ban":
                    try:
                        curr["sell"] = float(v) if v else 0.0
                    except Exception:
                        curr["sell"] = 0.0
            if curr.get("currency"):
                rates.append(curr)

    if not rates:
        raise RuntimeError("No currency rates extracted from Vietnam SBV contentFields.")

    usd = next((r for r in rates if r["currency"] == "USD"), None)
    if usd is None:
        raise RuntimeError("USD rate not found in Vietnam SBV data.")

    mid = (usd["buy"] + usd["sell"]) / 2.0

    # Prefer ref_date if parsed, otherwise fall back to real_date
    effective_date = (ref_date or real_date).strftime("%Y-%m-%d")

    return [{
        "country": "Vietnam",
        "value": mid,
        "unit": "VND",
        "date_of_page": exdate.strftime("%Y-%m-%d"),             # real SBV rate date
        "website": "https://sbv.gov.vn/vi/t%E1%BB%B7-gi%C3%A1-tham-kh%E1%BA%A3o-gi%E1%BB%AFa-%C4%91%E1%BB%93ng-vi%E1%BB%87t-nam-v%C3%A0-c%C3%A1c-lo%E1%BA%A1i-ngo%E1%BA%A1i-t%E1%BB%87-t%E1%BA%A1i-c%E1%BB%A5c-qu%E1%BA%A3n-l%C3%BD-ngo%E1%BA%A1i-h%E1%BB%91i",
        "Source": "SBV",
        "Status": "buy, sell",
    }]


# ## SRI LANKA(Central Bank of sri lanka)

# In[25]:


def scrape_sri_lanka(exdate=None):
    """
    Sri Lanka – Central Bank USD spot rate.
    Uses get_last_available_date to walk backwards until a day with data is found.
    """

    base_url = "https://www.cbsl.gov.lk/cbsl_custom/exrates/exrates_results_spot_mid.php"

    # 1) Decide starting date (requested or today) as 'YYYY-MM-DD'
    if exdate is None:
        start_date_str = datetime.utcnow().strftime("%Y-%m-%d")
    else:
        start_date_str = str(exdate).strip()  # expected 'YYYY-MM-DD'

    # 2) Define "has data" checker for a given date
    def sri_lanka_has_data(check_date: date) -> bool:
        date_str = check_date.strftime("%Y-%m-%d")
        payload = {
            "txtStart": date_str,
            "txtEnd": date_str,
            "chk_cur[]": "USD~US Dollar",
            "rangeType": "dates",
            "submit_button": "Submit",
            "lookupPage": "lookup_daily_exchange_rates.php",
            "startRange": "2006-11-11",
        }
        try:
            r = requests.post(base_url, headers=HEADERS, data=payload, timeout=15)
            if r.status_code != 200:
                return False

            soup = BeautifulSoup(r.text, "html.parser")
            table = soup.find("table")
            if table is None:
                return False

            rows = table.find_all("tr")
            if len(rows) < 2:
                return False

            tds = rows[1].find_all("td")
            if len(tds) < 2:
                return False

            rate_str = tds[1].get_text(strip=True)
            float(rate_str)  # just to confirm it's numeric
            return True
        except Exception:
            return False

    # 3) Find last date that actually has data
    real_date = get_last_available_date(start_date_str, sri_lanka_has_data)
    date_str = real_date.strftime("%Y-%m-%d")

    # 4) Fetch actual data for that date
    payload = {
        "txtStart": date_str,
        "txtEnd": date_str,
        "chk_cur[]": "USD~US Dollar",
        "rangeType": "dates",
        "submit_button": "Submit",
        "lookupPage": "lookup_daily_exchange_rates.php",
        "startRange": "2006-11-11",
    }

    r = requests.post(base_url, headers=HEADERS, data=payload, timeout=15)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.find("table")
    if table is None:
        raise RuntimeError("❌ No <table> in results — check date validity.")

    rows = table.find_all("tr")
    if len(rows) < 2:
        raise RuntimeError("❌ Table has no data rows.")

    tds = rows[1].find_all("td")
    if len(tds) < 2:
        raise RuntimeError(f"❌ Too few columns: {[t.get_text(strip=True) for t in tds]}")

    date_page = tds[0].get_text(strip=True)
    rate_str = tds[1].get_text(strip=True)

    try:
        page_date = datetime.strptime(date_page, "%Y-%m-%d").date()
        rate = float(rate_str)
    except Exception as e:
        raise RuntimeError(f"❌ Parse failed — date='{date_page}', rate='{rate_str}': {e}")

    print(f"✅ Sri Lanka: {rate} LKR/USD on {page_date}")

    return [{
        "country": "Sri Lanka",
        "value": rate,
        "unit": "LKR",
        "date_of_page": exdate.strftime("%Y-%m-%d"),
        "website": "https://www.cbsl.gov.lk/en/rates-and-indicators/exchange-rates/daily-indicative-usd-spot-exchange-rates",
        "Source": "Central Bank of Sri Lanka",
        "Status": "only one(USD->LKR)",
    }]


# # Turkey
# Türkiye Cumhuriyet Merkez Bankası (TCMB) or Central Bank of the Republic of Turkey (CBRT)

# In[26]:


def build_tcmb_url(date: datetime):
    year_month = date.strftime("%Y%m")
    day_full = date.strftime("%d%m%Y")
    return f"https://www.tcmb.gov.tr/kurlar/{year_month}/{day_full}.xml"


# In[27]:


def turkey_scrape(exdate=None, back_day=5):
    results = []

    exdate = normalize_date(exdate)
    base_date = datetime.combine(exdate, datetime.min.time())

    for i in range(back_day + 1):
        current_date = base_date - timedelta(days=i)
        url = build_tcmb_url(current_date)

        print(f"🔍 Checking: {current_date.strftime('%Y-%m-%d')}")
        print(f"🌐 URL: {url}")

        try:
            response = requests.get(url, timeout=10)

            if response.status_code != 200:
                print("❌ Not found → go back")
                continue

            # Parse XML
            root = ET.fromstring(response.content)

            currencies = root.findall("Currency")
            if not currencies:
                print("⚠️ No currency data → go back")
                continue

            usd_found = False

            for currency in currencies:
                name = currency.findtext("Isim")

                if name and "ABD DOLARI" in name:
                    buy = currency.findtext("BanknoteBuying")
                    sell = currency.findtext("BanknoteSelling")

                    # If data missing → treat as no data
                    if not buy or not sell:
                        print("⚠️ USD data missing → go back")
                        break

                    buy = float(buy)
                    sell = float(sell)
                    mid = (buy + sell) / 2

                    data = {
                        "country": "Turkey",
                        "unit": "TRY",
                        "value": mid,
                        "date_of_page": exdate.strftime("%Y-%m-%d"),
                        "website": url,
                        "Source": "Türkiye Cumhuriyet Merkez Bankası (TCMB)",
                        "Status": "Banknote(buy, sell)",
                    }

                    results.append(data)
                    print(f"✅ USD found: {mid} TRY")
                    return results  # STOP once found

                    usd_found = True

            if not usd_found:
                print("⚠️ USD not found → go back")

        except Exception as e:
            print(f"⚠️ Error: {e} → go back")

    print("❌ No data found in given range")
    return results


# # Egypt
# Central bank of Egypt

# In[28]:


def adjust_date_egypt(exdate):
    dt = normalize_date(exdate)
    return dt.strftime("%b/%m/%y")


# In[29]:


def egypt_scrape(exdate=None, back_day=10):
    options = Options()
    options.add_argument("--headless")  # Uncomment for headless mode
    driver = webdriver.Firefox(options=options)
    wait = WebDriverWait(driver, 20)
    results = []

    try:
        # Parse the target date
        target_date = adjust_date_egypt(exdate)
        print(f"🔍 Target date: {target_date}")

        # Get base date for calculations
        exdate = normalize_date(exdate)
        base_date = datetime.combine(exdate, datetime.min.time())

        # Navigate to the main page
        url = "https://www.cbe.org.eg/en/economic-research/statistics/cbe-exchange-rates/historical-data"
        print(f"🌐 Navigating to: {url}")
        driver.get(url)

        time_module.sleep(5)  # Give more time for page to load

        # Wait for page to fully load
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

        # Wait for the form to load
        wait.until(EC.presence_of_element_located((By.ID, "historicalDataForm")))

        # Iterate back through days to find data
        for days_back in range(back_day + 1):
            # Calculate date for this iteration
            current_date = base_date - timedelta(days=days_back)
            year = current_date.year
            month = current_date.strftime("%m")  # Month as number (01-12)
            month_name = current_date.strftime("%B")  # Full month name
            day = current_date.day

            current_date_str = f"{day:02d}/{month}/{year}" # Format: 01/02/2026

            print(f"\n📅 Attempting to select date: {current_date_str}")

            try:
                # Function to set date using JavaScript (cleaner approach)
                def set_date_via_javascript(element_id, date_str):
                    """Set date directly via JavaScript"""
                    js_script = f"""
                    document.getElementById("{element_id}").value = "{date_str}";
                    // Trigger change event
                    var event = new Event('change', {{ bubbles: true }});
                    document.getElementById("{element_id}").dispatchEvent(event);
                    """
                    driver.execute_script(js_script)
                    time_module.sleep(1)
                    print(f"✅ Set {element_id} to {date_str}")

                # Set From Date
                set_date_via_javascript("fromDate", current_date_str)

                # Set To Date (same date for single day)
                set_date_via_javascript("toDate", current_date_str)

                # Handle the "Select Option" dropdown - This is a button that opens a multiselect popup
                print("Handling currency selection dropdown...")

                # First, click the "Select Option" button to open the popup
                try:
                    select_option_button = driver.find_element(By.XPATH, '//*[@id="multipleSelectID_ms"]/span[1]/img')
                    print("Found 'Select Option' button")

                    # Scroll to the button
                    # driver.execute_script("arguments[0].scrollIntoView(true);", select_option_button)
                    time_module.sleep(1)
                    select_option_words = driver.find_element(By.XPATH, '//*[@id="multipleSelectID_ms"]/span[2]')
                    select_words = select_option_words.text.strip()
                    print(f"we have: {select_words}")
                    if select_words == "Select Option":
                        # Click to open the multiselect popup
                        select_option_button.click()

                        print("✅ Clicked 'Select Option' button to open popup")
                        time_module.sleep(2)

                        try:
                            select_usd = driver.find_element(By.XPATH, '//*[@id="ui-multiselect-0-multipleSelectID-option-0"]')
                            print(f"Found USD option with text: {select_usd.text}")
                            select_usd.click()
                            print("✅ Clicked USD option to select it")


                        except Exception as popup_error:
                            print(f"❌ Multiselect popup didn't appear: {popup_error}")

                    else:
                        print(f"✅ We already select currency: '{select_words}'")

                except Exception as button_error:
                    print(f"❌ Could not click 'Select Option' button: {button_error}")

                # Click the Show Data button
                try:
                    # Find the show button
                    show_button = driver.find_element(By.ID, "btnsubmit")

                    # Scroll to button
                    driver.execute_script("arguments[0].scrollIntoView(true);", show_button)
                    time_module.sleep(1)

                    # Click using JavaScript (more reliable)
                    driver.execute_script("arguments[0].click();", show_button)
                    print("✅ Show button clicked")

                    # Wait for results
                    print("⏳ Waiting for results...")
                    time_module.sleep(5)

                    # Check if there's data in the table
                    try:
                        # Look for the data table
                        table_selectors = [
                            (By.CLASS_NAME, "dynamic-data-table"),
                            (By.CLASS_NAME, "data-table"),
                            (By.TAG_NAME, "table"),
                            (By.XPATH, '//table[contains(@class, "table")]'),
                            (By.XPATH, '//div[contains(@class, "table-responsive")]//table')
                        ]

                        table = None
                        for selector_type, selector_value in table_selectors:
                            try:
                                table = driver.find_element(selector_type, selector_value)
                                if table.is_displayed():
                                    break
                            except:
                                continue

                        if table:
                            rows = table.find_elements(By.TAG_NAME, "tr")
                            print(f"Found table with {len(rows)} rows")

                            if len(rows) > 1:  # Has data (header + at least one row)
                                print(f"✅ Found data for {current_date_str}")

                                # Parse the table data
                                for i in range(1, len(rows)):
                                    cols = rows[i].find_elements(By.TAG_NAME, "td")
                                    if len(cols) >= 3:
                                        got_date = cols[0].text.strip()
                                        print(f"Row {i}: got_date={got_date}")
                                        buy = cols[2].text.strip()
                                        sell = cols[3].text.strip()
                                        rate = (float(buy) + float(sell)) / 2
                                        if got_date == current_date_str:
                                            results.append({
                                                "country": "Egypt",
                                                "unit": "EGP",
                                                'date_of_page': exdate.strftime("%Y-%m-%d"),
                                                'website': url,
                                                "value": rate,
                                                "Source": "Central bank of Egypt (CBE)",
                                                "Status": "buy sell",
                                            })

                                if results:  # If we found data, break
                                    print(f"✅ Successfully collected {len(results)} records")
                                    break
                            else:
                                print(f"❌ Table found but no data rows for {current_date_str}")

                        else:
                            print(f"❌ No table found for {current_date_str}")

                    except NoSuchElementException:
                        print(f"❌ No table found for {current_date_str}")

                    except Exception as e:
                        print(f"⚠️ Error parsing table: {e}")

                except Exception as button_error:
                    print(f"❌ Error with show button: {button_error}")

            except Exception as e:
                print(f"❌ Error processing date {current_date_str}: {str(e)[:100]}")
                continue

    except Exception as e:
        print(f"❌ General error: {e}")
        import traceback
        traceback.print_exc()

    finally:
        driver.quit()

    return results


# # Covert function

# In[30]:


import os
import pandas as pd
from datetime import datetime, timezone

CSV_FILE = "ManyExchangeRates.csv"

def write_to_csv(data):
    try:
        columns = [
            "country", "value", "unit",
            "website", "date_of_page",
            "date_of_scrape", "Source", "Status"
        ]

        # Add scrape_date to each row
        scrape_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for row in data:
            row["date_of_scrape"] = scrape_date

        # Read existing CSV if exists
        if os.path.exists(CSV_FILE):
            df = pd.read_csv(CSV_FILE)
        else:
            df = pd.DataFrame(columns=columns)

        # New data
        new_df = pd.DataFrame(data, columns=columns)

        # Append
        if df.empty:
            df = new_df
        else:
            df = pd.concat([df, new_df], ignore_index=True)

        # Remove duplicates
        df = df.drop_duplicates(subset=["country", "date_of_page"], keep="last")

        # Save to CSV
        df.to_csv(CSV_FILE, index=False)

    except Exception as e:
        print(f"❌ Error writing CSV: {e}")


# # Main function

# In[31]:


import os
import pandas as pd
from datetime import datetime, timezone

# -----------------------
# CONFIG
# -----------------------
OUTPUT_FILE = "ManyExchangeRates.csv"
TARGET_DATE = normalize_date(date.today() - timedelta(days=1))


# -----------------------
# MAIN PIPELINE
# -----------------------
def main():
    scraper_functions = [
        lambda: scrap_IMF(TARGET_DATE),
        lambda: scrape_hongkong(TARGET_DATE),
        lambda: scrape_cambodia(TARGET_DATE),
        lambda: scrape_lao(TARGET_DATE),
        lambda: scrape_myanmar(TARGET_DATE),
        lambda: scrape_pakistan(TARGET_DATE),
        lambda: scrape_vietnam(TARGET_DATE),
        lambda: scrape_sri_lanka(TARGET_DATE),
        lambda: egypt_scrape(TARGET_DATE),
        lambda: turkey_scrape(TARGET_DATE),
        lambda: indonesia_scrape(TARGET_DATE),
    ]

    rows = []
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for func in scraper_functions:
        try:
            result_list = func()

            if not result_list:
                continue

            for item in result_list:
                item["date_of_scrape"] = scraped_at
                rows.append(item)

                print(f"{item['country']} → {item['value']} ({item['unit']})")

        except Exception as e:
            print(f"ERROR in {func.__name__}: {e}")

    if not rows:
        print("No data scraped.")
        return

    # -----------------------
    # Build DataFrame safely
    # -----------------------
    df = pd.DataFrame(rows)

    # Ensure consistent column order
    df = df[
        [
            "country",
            "value",
            "unit",
            "website",
            "date_of_page",
            "date_of_scrape",
            "Source",
            "Status",
        ]
    ]

    # -----------------------
    # Save / Append
    # -----------------------
    if os.path.exists(OUTPUT_FILE):
        old_df = pd.read_csv(OUTPUT_FILE)

        # Fix pandas FutureWarning safely
        if old_df.empty:
            combined = df
        elif df.empty:
            combined = old_df
        else:
            combined = pd.concat([old_df, df], ignore_index=True)

        # Deduplicate
        combined.drop_duplicates(
            subset=["country", "date_of_page"],
            keep="last",
            inplace=True,
        )

        combined.to_csv(OUTPUT_FILE, index=False)

        print(f"Updated {OUTPUT_FILE} ({len(df)} new rows)")

    else:
        df.to_csv(OUTPUT_FILE, index=False)
        print(f"Created {OUTPUT_FILE} ({len(df)} rows)")


# -----------------------
# ENTRY POINT
# -----------------------
if __name__ == "__main__":
    main()

