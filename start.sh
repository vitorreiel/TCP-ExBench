#!/bin/bash
sudo apt install figlet -y > /dev/null 2>&1
echo -e "\n\e[1;34m$(figlet "TCP - E x Bench")\e[0m"
echo -e "\033[1;32m\n- [ Checking dependencies ] --------------------------------------------------------------------------------------- \033[0m"
sudo apt update > /dev/null 2>&1 && sudo apt install mininet openvswitch-switch python3-psutil python3-pandas python3-matplotlib  python3-seaborn -y > /dev/null 2>&1
echo -e "\033[1;32m\n- [ Starting ] ---------------------------------------------------------------------------------------------------- \033[0m"
sudo python3 script.py