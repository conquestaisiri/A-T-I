// ATI MT5 Expert Advisor
// Receives orders from Python bridge via file-based polling and executes via MT5 OrderSend
//
// Installation:
// 1. Copy this file to <MT5 Data Folder>/MQL5/Experts/ATI_EA.mq5
// 2. Compile in MetaEditor (F7)
// 3. Add to chart (input params: MagicNumber=123456)

#property copyright "ATI Trading Intelligence"
#property link      "https://github.com/ati"
#property version   "1.01"
#property strict

#include <Trade\Trade.mqh>

//--- Input parameters
input int      MagicNumber      = 123456;      // Magic number for ATI orders
input int      Deviation        = 10;          // Deviation in points
input bool     EnableLogging    = true;        // Enable detailed logging
input string   AllowedSymbols   = "";          // Comma-separated allowed symbols (empty = all)

//--- Global variables
CTrade         trade;
string         g_allowed_symbols[];
bool           g_initialized = false;

//--- Order request structure from Python bridge
struct OrderRequest
{
   string       order_id;   // client order id echoed back verbatim (may be a UUID string)
   string       symbol;
   int          order_type;    // MQL5 order type (0=BUY, 1=SELL, 2=BUY_LIMIT, 3=SELL_LIMIT)
   double       volume;
   double       price;
   double       sl;
   double       tp;
   int          deviation;
   int          magic;
   string       comment;
};

//--- Forward declarations
void CheckForOrders();
bool ParseOrderRequest(const string& json, OrderRequest& request);
bool ExecuteOrder(const OrderRequest& request, MqlTradeResult& result);
string CreateResponse(bool success, const string& order_id, const string& message, const MqlTradeResult& result);
void WriteResponse(const string& file, const string& content);
void LogMessage(const string msg);
double GetJSONValue(const string& json, const string& key);
string GetJSONString(const string& json, const string& key);

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   // Initialize trade object
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetAsyncMode(false); // Synchronous for reliability

   // Parse allowed symbols
   if(StringLen(AllowedSymbols) > 0)
   {
      int count = StringSplit(AllowedSymbols, ',', g_allowed_symbols);
      LogMessage("Allowed symbols parsed: " + IntegerToString(count));
   }

   g_initialized = true;
   LogMessage("ATI EA initialized with MagicNumber=" + IntegerToString(MagicNumber));

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   g_initialized = false;
   LogMessage("ATI EA deinitialized: " + IntegerToString(reason));
}

//+------------------------------------------------------------------+
//| Expert tick function - polls for orders from the Python bridge   |
//+------------------------------------------------------------------+
void OnTick()
{
   if(!g_initialized)
      return;

   CheckForOrders();
}

//+------------------------------------------------------------------+
//| ChartEvent function                                              |
//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long& lparam, const double& dparam, const string& sparam)
{
   // Handle chart events if needed
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

   string result_message = "OK";
   if(!success)
      result_message = "Execution failed";

   string response = CreateResponse(success, request.order_id, result_message, result);
   WriteResponse(response_file, response);
}

//+------------------------------------------------------------------+
//| Parse order request from JSON                                     |
//+------------------------------------------------------------------+
bool ParseOrderRequest(const string& json, OrderRequest& request)
{
   request.order_id = GetJSONString(json, "order_id");
   request.symbol = GetJSONString(json, "symbol");
   request.order_type = (int)GetJSONValue(json, "order_type");
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
   // Pending (limit) orders require TRADE_ACTION_PENDING; market orders use DEAL.
   if(request.order_type == 2 || request.order_type == 3)
      trade_request.action = TRADE_ACTION_PENDING;
   else
      trade_request.action = TRADE_ACTION_DEAL;
   trade_request.symbol = request.symbol;
   trade_request.volume = request.volume;
   trade_request.type = (ENUM_ORDER_TYPE)request.order_type;
   trade_request.sl = request.sl;
   trade_request.tp = request.tp;
   trade_request.deviation = request.deviation > 0 ? request.deviation : Deviation;
   trade_request.magic = request.magic > 0 ? request.magic : MagicNumber;
   trade_request.comment = request.comment;
   trade_request.type_filling = ORDER_FILLING_FOK;
   trade_request.type_time = ORDER_TIME_GTC;

   // For market orders (type 0=BUY, 1=SELL), use current price if not provided
   if(request.price <= 0.0)
   {
      if(request.order_type == 1)
         trade_request.price = SymbolInfoDouble(request.symbol, SYMBOL_BID);
      else
         trade_request.price = SymbolInfoDouble(request.symbol, SYMBOL_ASK);
   }
   else
   {
      trade_request.price = request.price;
   }

   // Send order
   bool sent = OrderSend(trade_request, result);

   // OrderSend returns true once the trade server responds, but the retcode
   // carries the definitive outcome: a rejection still yields sent=true.
   bool accepted = sent && (result.retcode == TRADE_RETCODE_DONE ||
                            result.retcode == TRADE_RETCODE_DONE_PARTIAL ||
                            result.retcode == TRADE_RETCODE_PLACED);

   if(sent)
   {
      LogMessage("ORDER SENT: " + request.symbol + " type=" + IntegerToString(request.order_type) + " vol=" + DoubleToString(request.volume, 2) + " retcode=" + IntegerToString(result.retcode));
   }
   else
   {
      LogMessage("ORDER FAILED: " + request.symbol + " retcode=" + IntegerToString(GetLastError()));
   }

   return accepted;
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
   json += ",\"retcode\":" + IntegerToString(result.retcode);
   json += ",\"deal\":" + IntegerToString((int)result.deal);
   json += ",\"order\":" + IntegerToString((int)result.order);
   json += ",\"volume\":\"" + DoubleToString(result.volume, 2) + "\"";
   json += ",\"price\":\"" + DoubleToString(result.price, 5) + "\"";
   json += "}";
   return json;
}

void LogMessage(const string msg)
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
   if(pos < StringLen(json) && json[pos] == '-')
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