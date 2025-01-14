from xmlrpc.client import ServerProxy 
import os
import time


class CreateDirectoryClient: 
    def __init__(self, server_address: str) -> None:
        """
        Initialize the directory client.
        
        Args:
            server_address (str): The address of the XML-RPC server
        """
        self.server_address = server_address
        self.username = os.getenv('USER')
        
    def create(self, dirname: str) -> tuple[bool, str]:
        """
        Create a directory on the remote server.
        
        Args:
            dirname (str): Name of the directory to create
            
        Returns:
            tuple[bool, str]: A tuple containing:
                - bool: Success status (True if successful, False otherwise)
                - str: Error message if failed, empty string if successful
        """
        try:
            server = ServerProxy(self.server_address)
            ret, msg = server.mxcube_createdir(dirname, self.username)
            
            if isinstance(ret, str) and ret.lower() == 'error':
                return False, msg
            else:
                return True, ""
                
        except Exception as e:  # Using more specific Exception instead of BaseException
            return False, f"error talking with mxcube_createdir_server: {e}"  # Using f-string

"""
if __name__ == '__main__':
   dirname = '2020_04_01'
   fulldir = os.path.join("/data4/proxima1-soleil/2020_Run2", dirname)
   client = CreateDirectoryClient("http://localhost:9023")
   client.create(fulldir)
   t0 = time.time()
   while True:
      if os.path.exists(fulldir):
          break
      time.sleep(0.05)

   print "Elapsed = ", time.time() -t0
"""

