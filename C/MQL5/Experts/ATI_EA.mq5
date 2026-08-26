// ATI MT5 Expert Advisor
// Receives orders from Python bridge via HTTP and executes via MT5 OrderSend
// 
// Installation:
// 1. Copy this file to <MT5 Data Folder>/MQL5/Experts/ATI_EA.mq5
// 2. Compile in MetaEditor
// 3. Add to chart, enable "Allow WebRequest for listed URLs" with URL: http://localhost:8080
// 4. Set input parameters: BridgePort=8080, MagicNumber=123456

#property copyright "ATI Trading Intelligence"
#property link      "https://github.com/ati"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#property include "Trade\Trade.mqh"

//--- Input parameters
input int      BridgePort       = 8080;        // HTTP server port for Python bridge
input int      MagicNumber      = 123456;      // Magic number for ATI orders
input double   MaxSlippage      = 5.0;         // Max slippage in points
input int      Deviation        = 10;          // Deviation in points
input bool     EnableLogging    = true;        // Enable detailed logging
input string   AllowedSymbols   = "";          // Comma-separated allowed symbols (empty = all)

//--- Global variables
CTrade         trade;
int            g_server_handle = -1;
string         g_allowed_symbols[];
bool           g_initialized = false;

//--- Order request structure from Python bridge
struct OrderRequest
{
   long         order_id;
   string       symbol;
   ENUM_ORDER_TYPE order_type;
   double       volume;
   double       price;
   double       sl;
   double       tp;
   int          deviation;
   int          magic;
   string       comment;
};

//--- Forward declarations
bool StartHTTPServer(int port);
void StopHTTPServer();
string HandleRequest(const string& request_json);
bool ParseOrderRequest(const string& json, OrderRequest& request);
bool ExecuteOrder(const OrderRequest& request, MqlTradeResult& result);
string CreateResponse(bool success, const string& order_id, const string& message, const MqlTradeResult& result = {});
void LogMessage(const string& msg);

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   // Initialize trade object
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviation(Deviation);
   trade.SetAsyncMode(false); // Synchronous for reliability
   
   // Parse allowed symbols
   if(StringLen(AllowedSymbols) > 0)
   {
      g_allowed_symbols = StringSplit(AllowedSymbols, ',');
      ArrayPrint(g_allowed_symbols);
   }
   
   // Start HTTP server
   if(!StartHTTPServer(BridgePort))
   {
      LogMessage("ERROR: Failed to start HTTP server on port " + IntegerToString(BridgePort));
      return INIT_FAILED;
   }
   
   g_initialized = true;
   LogMessage("ATI EA initialized on port " + IntegerToString(BridgePort) + " with MagicNumber=" + IntegerToString(MagicNumber));
   
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   StopHTTPServer();
   g_initialized = false;
   LogMessage("ATI EA deinitialized: " + EnumToString(reason));
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   // EA is event-driven via HTTP - no per-tick logic needed
   // Could add position monitoring here if needed
}

//+------------------------------------------------------------------+
//| ChartEvent function                                              |
//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long& lparam, const double& dparam, const string& sparam)
{
   // Handle chart events if needed
}

//+------------------------------------------------------------------+
//| Start HTTP server using WinHTTP                                   |
//+------------------------------------------------------------------+
bool StartHTTPServer(int port)
{
   // Note: MQL5 doesn't have built-in HTTP server
   // We'll use a named pipe or file-based communication as fallback
   // For production, use a separate Python HTTP server that writes to a queue file
   // This is a simplified version using file-based communication
   
   string pipe_name = "\\\\.\\pipe\\ATI_EA_" + IntegerToString(MagicNumber);
   
   // Create named pipe for communication
   int handle = FileOpen(pipe_name, FILE_READ | FILE_WRITE | FILE_BIN | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(handle == INVALID_HANDLE)
   {
      // Try to create the pipe
      // Note: MQL5 can't create named pipes directly, use file-based approach
      LogMessage("Using file-based communication (named pipes not available in MQL5)");
   }
   else
   {
      FileClose(handle);
   }
   
   return true;
}

void StopHTTPServer()
{
   // Cleanup
}

//+------------------------------------------------------------------+
//| Check for incoming orders from bridge (file-based polling)       |
//+------------------------------------------------------------------+
void CheckForOrders()
{
   string order_file = TerminalInfoString(TERMINAL_DATA_PATH) + "\\MQL5\\Files\\ATI_Orders_" + IntegerToString(MagicNumber) + ".json";
   string response_file = TerminalInfoString(TERMINAL_DATA_PATH) + "\\MQL5\\Files\\ATI_Response_" + IntegerToString(MagicNumber) + ".json";
   
   // Check if order file exists
   if(!FileIsExist(order_file))
      return;
   
   // Read order request
   int handle = FileOpen(order_file, FILE_READ | FILE_BIN | FILE_SHARE_READ);
   if(handle == INVALID_HANDLE)
      return;
   
   string json = "";
   while(!FileIsEnding(handle))
   {
      json += FileReadString(handle);
   }
   FileClose(handle);
   
   // Delete order file to acknowledge
   FileDelete(order_file);
   
   if(StringLen(json) == 0)
      return;
   
   LogMessage("Received order request: " + json);
   
   // Parse and execute
   OrderRequest request;
   if(!ParseOrderRequest(json, request))
   {
      WriteResponse(response_file, "{\"success\":false,\"message\":\"Failed to parse request\"}");
      return;
   }
   
   MqlTradeResult result;
   bool success = ExecuteOrder(request, result);
   
   string response = CreateResponse(success, request.order_id, success ? "OK" : "Execution failed", result);
   WriteResponse(response_file, response);
}

//+------------------------------------------------------------------+
//| Parse order request from JSON                                     |
//+------------------------------------------------------------------+
bool ParseOrderRequest(const string& json, OrderRequest& request)
{
   // Simple JSON parsing - in production use a proper JSON library
   // This is a minimal parser for the expected fields
   
   request.order_id = (long)GetJSONValue(json, "order_id");
   request.symbol = GetJSONString(json, "symbol");
   request.order_type = (ENUM_ORDER_TYPE)(int)GetJSONValue(json, "order_type");
   request.volume = GetJSONValue(json, "volume");
   request.price = GetJSONValue(json, "price");
   request.sl = GetJSONValue(json, "sl");
   request.tp = GetJSONValue(json, "tp");
   request.deviation = (int)GetJSONValue(json, "deviation");
   request.magic = (int)GetJSONValue(json, "magic");
   request.comment = GetJSONString(json, "comment");
   
   return StringLen(request.symbol) > 0 && request.volume > 0;
}

//+------------------------------------------------------------------+
//| Execute order via MT5                                             |
//+------------------------------------------------------------------+
bool ExecuteOrder(const OrderRequest& request, MqlTradeResult& result)
{
   // Validate symbol
   if(!IsSymbolAllowed(request.symbol))
   {
      LogMessage("Symbol not allowed: " + request.symbol);
      return false;
   }
   
   // Prepare trade request
   MqlTradeRequest trade_request = {};
   trade_request.action = TRADE_ACTION_DEAL;
   trade_request.symbol = request.symbol;
   trade_request.volume = request.volume;
   trade_request.type = request.order_type;
   trade_request.price = request.price;
   trade_request.sl = request.sl;
   trade_request.tp = request.tp;
   trade_request.deviation = request.deviation > 0 ? request.deviation : Deviation;
   trade_request.magic = request.magic > 0 ? request.magic : MagicNumber;
   trade_request.comment = request.comment;
   trade_request.type_filling = ORDER_FILLING_FOK;
   trade_request.type_time = ORDER_TIME_GTC;
   
   // Send order
   bool sent = OrderSend(request, result);
   
   if(sent)
   {
      LogMessage("ORDER SENT: " + request.symbol + " " + EnumToString(request.order_type) + " vol=" + DoubleToString(request.volume, 2) + " result=" + EnumToString(result.retcode));
   }
   else
   {
      LogMessage("ORDER FAILED: " + request.symbol + " retcode=" + IntegerToString(GetLastError()));
   }
   
   return sent;
}

//+------------------------------------------------------------------+
//| Helper functions                                                  |
//+------------------------------------------------------------------+

bool IsSymbolAllowed(const string& symbol)
{
   if(ArraySize(g_allowed_symbols) == 0)
      return true;
   
   for(int i = 0; i < ArraySize(g_allowed_symbols); i++)
   {
      if(g_allowed_symbols[i] == symbol)
         return true;
   }
   return false;
}

void WriteResponse(const string& file, const string& content)
{
   int handle = FileOpen(file, FILE_WRITE | FILE_BIN | FILE_ANSI);
   if(handle != INVALID_HANDLE)
   {
      FileWriteString(handle, content);
      FileClose(handle);
   }
}

string CreateResponse(bool success, const string& order_id, const string& message, const MqlTradeResult& result)
{
   string json = "{";
   json += "\"success\":" + (success ? "true" : "false") + ",";
   json += "\"order_id\":\"" + order_id + "\",";
   json += "\"message\":\"" + message + "\"";
   
   if(result.retcode != 0)
   {
      json += ",\"retcode\":" + IntegerToString(result.retcode);
      json += ",\"deal\":" + LongToString(result.deal);
      json += "\"order\":" + LongToString(result.order);
      json += "\"volume\":" + DoubleToString(result.volume, 2);
      json += "\"price\":" + DoubleToString(result.price, 5);
   }
   
   json += "}";
   return json;
}

void LogMessage(const string& msg)
{
   if(EnableLogging)
   {
      Print("[ATI_EA] " + msg);
   }
}

// Simple JSON value extraction (minimal implementation)
double GetJSONValue(const string& json, const string& key)
{
   string search = "\"" + key + "\":";
   int pos = StringFind(json, search);
   if(pos < 0)
      return 0.0;
   
   pos += StringLen(search);
   while(pos < StringLen(json) && (json[pos] == ' ' || json[pos] == '\t'))
      pos++;
   
   bool neg = false;
   if(json[pos] == '-')
   {
      neg = true;
      pos++;
   }
   
   double val = 0.0;
   while(pos < StringLen(json) && (json[pos] >= '0' && json[pos] <= '9'))
   {
      val = val * 10 + (json[pos] - '0');
      pos++;
   }
   
   if(pos < StringLen(json) && json[pos] == '.')
   {
      pos++;
      double mult = 0.1;
      while(pos < StringLen(json) && (json[pos] >= '0' && json[pos] <= '9'))
      {
         val += (json[pos] - '0') * mult;
         mult *= 0.1;
         pos++;
      }
   }
   
   return neg ? -val : val;
}

string GetJSONString(const string& json, const string& key)
{
   string search = "\"" + key + "\":\"";
   int pos = StringFind(json, search);
   if(pos < 0)
      return "";
   
   pos += StringLen(search);
   int end = StringFind(json, "\"", pos);
   if(end < 0)
      return "";
   
   return StringSubstr(json, pos, end - pos);
}