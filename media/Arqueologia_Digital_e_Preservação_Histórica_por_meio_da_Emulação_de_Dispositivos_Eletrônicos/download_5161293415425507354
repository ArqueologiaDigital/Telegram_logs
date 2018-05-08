int AddressPin[8] { 2,3,4,5,6,7,8,9 };
int DataPin[8] { 10,11,12,13,A0,A1,A2,A3 };

void setup() 
{
  Serial.begin(9600);
    
  for(int i = 0; i<8; i++)
  {
    pinMode(AddressPin[i], OUTPUT);
    digitalWrite(AddressPin[i], LOW);
  }
  
  for(int i = 0; i<8; i++)
  {
    pinMode(DataPin[i], INPUT);
  }
  delay(10000);
}

int currentAddress = 0;

void loop() {
   
  while(currentAddress < 256)
  {
    
    for(int i = 0; i < 8; i++)
    {
      if(bitRead(currentAddress, i) ==1)
      {
        digitalWrite(AddressPin[i],HIGH);
 
        
      }else
      {
        digitalWrite(AddressPin[i],LOW);
      }
    }

    delay(100);
    
    int data = 0;
    
    for (int i=0; i<8; i++){
      if (digitalRead(DataPin[i]) == HIGH){
        data |= (1 << i);
      }
    }
    
    Serial.print(data,HEX);
    
    currentAddress++; 
  }
}
