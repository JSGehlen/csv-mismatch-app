import pandas as pd
from rapidfuzz import process, fuzz
import streamlit as st
import openai
import re

# === Helpers ===
def normalize(text):
    if not isinstance(text, str): return ''
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s]', '', text)
    return re.sub(r'\s+', ' ', text).strip()

def fuzzy_match(guess, titles, title_map, slug_map, threshold=82):
    guess_norm = normalize(guess)
    match, score, _ = process.extractOne(guess_norm, titles, scorer=fuzz.token_set_ratio)
    if score >= threshold:
        return title_map[match], slug_map[match], score, 'token_set_ratio'
    match, score, _ = process.extractOne(guess_norm, titles, scorer=fuzz.partial_ratio)
    if score >= 90:
        return title_map[match], slug_map[match], score, 'partial_ratio'
    return None, None, None, None

def translate_with_openai(text, api_key):
    openai.api_key = api_key
    prompt = f"Translate this product title from German to English: '{text}'"
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=50
        )
        return response['choices'][0]['message']['content'].strip()
    except Exception as e:
        return f"TRANSLATION_FAILED: {e}"

# === Session state ===
st.set_page_config(layout="wide")
st.title("🔁 Shopify Redirect Matching (OpenAI Version)")

if "translated" not in st.session_state:
    st.session_state.translated = None
if "matched" not in st.session_state:
    st.session_state.matched = None
if "manual_results" not in st.session_state:
    st.session_state.manual_results = []

# === Step 1: Upload + OpenAI API Key ===
st.header("Step 1: Upload Files and Add API Key")
col1, col2 = st.columns(2)
with col1:
    broken_file = st.file_uploader("Upload broken_links.csv", type="csv")
with col2:
    product_file = st.file_uploader("Upload product_titles.csv", type="csv")

api_key = st.text_input("🔑 Enter your OpenAI API key", type="password")

if broken_file and product_file and api_key:
    broken_df = pd.read_csv(broken_file)
    product_df = pd.read_csv(product_file)
    st.success("✅ Files and API key loaded!")

    # === Step 2: Translate Slugs ===
    st.header("Step 2: Translate German Slugs")
    if st.button("Translate with OpenAI"):
        broken_df['slug'] = broken_df['Redirect from'].str.extract(r'/de/products/(.*)')
        broken_df['clean_slug'] = broken_df['slug'].str.replace('-', ' ', regex=False)

        translated = []
        for idx, row in broken_df.iterrows():
            result = translate_with_openai(row['clean_slug'], api_key)
            translated.append(result)
            st.write(f"{idx + 1}: {row['clean_slug']} → {result}")
        broken_df['translated_guess'] = translated
        st.session_state.translated = broken_df
        st.success("✅ Translations completed.")

# === Step 3: Match Translations ===
if st.session_state.translated is not None:
    st.header("Step 3: Auto-Match Translated Titles")

    # Normalize product titles
    product_df['normalized_title'] = product_df['Product Title'].apply(normalize)
    title_map = dict(zip(product_df['normalized_title'], product_df['Product Title']))
    slug_map = dict(zip(product_df['normalized_title'], product_df['Product URL slug']))
    titles = list(title_map.keys())

    matches, unmatched = [], []

    for _, row in st.session_state.translated.iterrows():
        guess = row['translated_guess']
        redirect_from = row['Redirect from']
        if guess.startswith("TRANSLATION_FAILED"):
            unmatched.append(row)
            continue
        title, slug, score, method = fuzzy_match(guess, titles, title_map, slug_map)
        if slug:
            matches.append({
                "Redirect from": redirect_from,
                "Redirect to": slug,
                "Matched title": title,
                "Match score": score,
                "Match method": method
            })
        else:
            unmatched.append(row)

    st.session_state.matched = {
        "final": pd.DataFrame(matches),
        "unmatched": pd.DataFrame(unmatched)
    }

    st.success(f"✅ Matched {len(matches)} | ❌ Unmatched: {len(unmatched)}")

# === Step 4: Manual Review ===
if st.session_state.matched is not None:
    st.header("Step 4: Manual Review of Unmatched")

    unmatched_df = st.session_state.matched["unmatched"]
    max_index = len(unmatched_df)

    if "manual_index" not in st.session_state:
        st.session_state.manual_index = 0

    idx = st.session_state.manual_index

    if idx < max_index:
        row = unmatched_df.iloc[idx]
        guess = normalize(row['translated_guess'])
        redirect_from = row['redirect_from']

        st.subheader(f"🔍 {idx + 1}/{max_index}: {row['translated_guess']}")
        top_matches = process.extract(guess, titles, scorer=fuzz.token_set_ratio, limit=3)

        options = []
        for title_norm, score, _ in top_matches:
            title = title_map[title_norm]
            slug = slug_map[title_norm]
            options.append(f"{title} → {slug} (score: {score})")

        choice = st.radio("Select a match or skip:", options + ["❌ Skip this one"])

        if st.button("Save & Next"):
            if choice != "❌ Skip this one":
                slug = choice.split("→")[-1].split("(")[0].strip()
                st.session_state.manual_results.append({
                    "Redirect from": redirect_from,
                    "Redirect to": slug
                })
            st.session_state.manual_index += 1
            st.experimental_rerun()
    else:
        st.success("✅ Manual review complete!")

# === Step 5: Export Final Redirect File ===
if st.session_state.matched is not None:
    st.header("Step 5: Download All Redirects")

    final = st.session_state.matched["final"]
    manual = pd.DataFrame(st.session_state.manual_results)
    merged = pd.concat([final[["Redirect from", "Redirect to"]], manual], ignore_index=True)
    merged = merged.drop_duplicates(subset=["Redirect from"])

    st.download_button("⬇️ Download all_redirects.csv", merged.to_csv(index=False), file_name="all_redirects.csv")