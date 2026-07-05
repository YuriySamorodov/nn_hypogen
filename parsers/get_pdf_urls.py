#!/usr/bin/env python3
"""
Script to extract PDF URLs from sci-hub.json based on DOI identifiers.
Generates Sci-Hub URLs for each publication in the JSON file.
"""

import json
import sys
from pathlib import Path


def load_sci_hub_json(filepath: str) -> dict:
    """Load and parse the sci-hub.json file."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: File not found: {filepath}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {filepath}: {e}", file=sys.stderr)
        sys.exit(1)


def generate_pdf_urls(data: dict, sci_hub_domain: str = "sci-hub.se") -> list:
    """Generate PDF download URLs from DOI entries."""
    results = []
    
    query = data.get("query", "")
    items = data.get("results", [])
    
    for item in items:
        doi = item.get("doi", "")
        title = item.get("title", "")
        year = item.get("year", "")
        journal = item.get("journal", "")
        score = item.get("score", 0)
        
        if not doi:
            continue
        
        # Generate Sci-Hub PDF URL
        pdf_url = f"https://{sci_hub_domain}/{doi}"
        
        results.append({
            "doi": doi,
            "title": title,
            "year": year,
            "journal": journal,
            "score": score,
            "pdf_url": pdf_url
        })
    
    return results


def save_results(results: list, output_file: str = "pdf_urls.txt"):
    """Save results as plain text URLs."""
    with open(output_file, 'w', encoding='utf-8') as f:
        for item in results:
            f.write(item["pdf_url"] + "\n")


def save_json_results(results: list, output_file: str = "pdf_urls.json"):
    """Save results as JSON with metadata."""
    output = {
        "total": len(results),
        "results": results
    }
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)


def save_csv_results(results: list, output_file: str = "pdf_urls.csv"):
    """Save results as CSV."""
    import csv
    
    with open(output_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=["doi", "title", "year", "journal", "score", "pdf_url"])
        writer.writeheader()
        writer.writerows(results)


def main():
    # Adjust path relative to current directory
    input_file = "sci-hub.json"
    
    if not Path(input_file).exists():
        print(f"Error: {input_file} not found in current directory", file=sys.stderr)
        sys.exit(1)
    
    data = load_sci_hub_json(input_file)
    results = generate_pdf_urls(data)
    
    if not results:
        print("Warning: No valid DOI entries found", file=sys.stderr)
        sys.exit(1)
    
    # Save in multiple formats
    save_results(results, "pdf_urls.txt")
    save_json_results(results, "pdf_urls.json")
    save_csv_results(results, "pdf_urls.csv")
    
    print(f"Successfully processed {len(results)} entries")
    print(f"Output files: pdf_urls.txt, pdf_urls.json, pdf_urls.csv")
    print("\nFirst 5 PDF URLs:")
    for i, item in enumerate(results[:5]):
        print(f"  {i+1}. {item['pdf_url']}")


if __name__ == "__main__":
    main()