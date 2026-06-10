import win32com.client
import os
import argparse

def refresh_excel_formulas(file_name):
    # Automatically append .xlsx if the user didn't type it
    if not file_name.endswith('.xlsx'):
        file_name += '.xlsx'
        
    # Ensure we have an absolute path, as COM requires it
    abs_path = os.path.abspath(file_name)
    
    if not os.path.exists(abs_path):
        print(f"Error: The file '{abs_path}' does not exist.")
        return

    print(f"Opening and refreshing: {abs_path}")
    
    # Start an instance of Excel in the background
    excel = win32com.client.Dispatch("Excel.Application")
    excel.Visible = False 
    excel.DisplayAlerts = False 
    
    try:
        wb = excel.Workbooks.Open(abs_path)
        excel.CalculateFull()
        wb.Save()
        wb.Close()
        print("Successfully refreshed and saved!")
        
    except Exception as e:
        print(f"Error refreshing Excel file: {e}")
        
    finally:
        excel.Quit()

if __name__ == "__main__":
    # Set up argument parsing
    parser = argparse.ArgumentParser(description="Refresh formulas in an Excel file.")
    parser.add_argument(
        "filename", 
        help="The name of the Excel file to refresh (e.g., 'ETFs' or 'ETFs.xlsx')"
    )
    
    args = parser.parse_args()
    
    # Run the function with the provided parameter
    refresh_excel_formulas(args.filename)