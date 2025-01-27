from mininet.net import Mininet
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.cli import CLI
from mininet.node import Node
from time import sleep
import psutil
import csv
import json
import os
import pandas as pd
import matplotlib.pyplot as plt
from colorama import Fore, Style

class LinuxRouter(Node):
    def config(self, **params):
        super(LinuxRouter, self).config(**params)
        self.cmd("sysctl -w net.ipv4.ip_forward=1")
        self.cmd("sysctl -w net.ipv6.conf.all.forwarding=1")
    
    def terminate(self):
        self.cmd("sysctl -w net.ipv4.ip_forward=0")
        self.cmd("sysctl -w net.ipv6.conf.all.forwarding=0")
        super(LinuxRouter, self).terminate()

def create_topology(loss, delay):
    """Create a simple Mininet topology with 2 routers and 2 hosts."""
    net = Mininet(link=TCLink)

    print("Creating network topology...\n")
    
    # Add routers
    r1 = net.addHost("r1", cls=LinuxRouter, ip="10.0.1.1/24")
    r2 = net.addHost("r2", cls=LinuxRouter, ip="10.0.2.1/24")

    # Add hosts with IPv4 configuration
    h1 = net.addHost("h1", ip="10.0.1.2/24", defaultRoute="via 10.0.1.1")
    h2 = net.addHost("h2", ip="10.0.2.2/24", defaultRoute="via 10.0.2.1")

    # Link hosts to routers
    net.addLink(h1, r1) 
    net.addLink(h2, r2)

    # Link routers
    net.addLink(r1, r2, loss=loss, delay=f'{delay}ms', intfName1="r1-eth1", intfName2="r2-eth1")

    r1.setIP("192.168.1.1/30", intf="r1-eth1")
    r2.setIP("192.168.1.2/30", intf="r2-eth1")

    r1.cmd("ip -6 addr add 2001:db8:1::1/64 dev r1-eth1")
    r2.cmd("ip -6 addr add 2001:db8:1::2/64 dev r2-eth1")

    h1.cmd("ip -6 addr add 2001:db8:0:1::2/64 dev h1-eth0")
    h2.cmd("ip -6 addr add 2001:db8:0:2::2/64 dev h2-eth0")
    h1.cmd("ip -6 route add default via 2001:db8:0:1::1")
    h2.cmd("ip -6 route add default via 2001:db8:0:2::1")

    r1.cmd("ip -6 addr add 2001:db8:0:1::1/64 dev r1-eth0")
    r2.cmd("ip -6 addr add 2001:db8:0:2::1/64 dev r2-eth0")

    r1.cmd("ip route add 10.0.2.0/24 via 192.168.1.2")
    r2.cmd("ip route add 10.0.1.0/24 via 192.168.1.1")

    r1.cmd("ip -6 route add 2001:db8:0:2::/64 via 2001:db8:1::2")
    r2.cmd("ip -6 route add 2001:db8:0:1::/64 via 2001:db8:1::1")

    net.start()

    return net, h1, h2

def configure_tcp_version(host, tcp_version):
    """Configure TCP version for the given host."""
    host.cmd(f"sysctl -w net.ipv4.tcp_congestion_control={tcp_version}")

def measure_metrics(net, h1, h2, output_csv, output_log, test_id, tcp_version, ip_version):

    configure_tcp_version(h1, tcp_version)
    configure_tcp_version(h2, tcp_version)

    # Start iperf server on h2
    if ip_version == "IPv6":
        h2.cmd("iperf3 -s -6 -p 5202 &")
    else:
        h2.cmd("iperf3 -s -p 5201 &")
    sleep(2)  # Give the server time to start

    metrics = []
    os.makedirs("output", exist_ok=True)

    with open(output_log, 'a') as log_file:
        print("Running iperf test...")
        log_file.write(f"Running iperf test for {tcp_version} with {ip_version}...\n")

        cpu_usage_before = psutil.cpu_percent(interval=1)

        if ip_version == "IPv6":
            iperf_result = h1.cmd(f"iperf3 -c 2001:db8:0:2::2%h1-eth0 -6 -p 5202 -t 30 -b {int(bw / 1e3)}G -P {connections} -J")
        else:
            iperf_result = h1.cmd(f"iperf3 -c {h2.IP()} -p 5201 -t 30 -b {int(bw / 1e3)}G -P {connections} -J")

        cpu_usage_after = psutil.cpu_percent(interval=1)
        avg_cpu_usage = round((cpu_usage_before + cpu_usage_after) / 2, 2)

        log_file.write(iperf_result)
        log_file.write("\n")

        try:
            iperf_data = json.loads(iperf_result)
            if connections > 1:
                streams = iperf_data['end']['streams']
                throughput_bps = sum(stream['receiver']['bits_per_second'] for stream in streams) / connections
            else:
                throughput_bps = iperf_data['end']['sum_received']['bits_per_second']
            throughput_gbps = round(throughput_bps / 1e9, 2)
            retransmissions = iperf_data['end']['sum_sent']['retransmits']
            mean_rtt = iperf_data['end']['streams'][0]['sender'].get('mean_rtt', 0)
            total_bytes_sent = iperf_data['end']['sum_sent']['bytes']
            tcp_mss = iperf_data['start']['tcp_mss_default']
            total_packets_sent = round(total_bytes_sent / tcp_mss, 2)
            packet_loss = "{:.2f}".format((retransmissions / total_packets_sent) * 100 if total_packets_sent > 0 else 0)

            metrics.append({
                'ID': test_id,
                'TCP Version': tcp_version,
                'IP Version': ip_version,
                'Throughput (Gbps)': throughput_gbps,
                'Packet Loss (%)': packet_loss,
                'Mean RTT (ms)': mean_rtt,
                'Retransmissions': retransmissions,
                'CPU Usage (%)': avg_cpu_usage
            })

        except KeyError as e:
            log_file.write(f"Error: Missing key {str(e)} in iperf result.\n")

    os.makedirs("dataset", exist_ok=True)
    output_filename = f"dataset/dataset_{ip_version.lower()}_{tcp_version.lower()}.csv"
    with open(output_filename, 'a', newline='') as csvfile:
        fieldnames = [
            'ID', 'TCP Version', 'IP Version', 'Throughput (Gbps)', 'Packet Loss (%)',
            'Mean RTT (ms)', 'Retransmissions', 'CPU Usage (%)'
        ]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        if csvfile.tell() == 0:
            writer.writeheader()
        writer.writerows(metrics)

def cleanup(net):
    """Stop the Mininet network and clean up processes."""
    print("Stopping network...")
    net.stop()
    os.system("pkill -f iperf3")

def clear_datasets():
    """Clear existing dataset files."""
    datasets_path = "dataset"
    os.makedirs(datasets_path, exist_ok=True)  # Ensure the directory exists

    for file in os.listdir(datasets_path):
        if file.endswith(".csv"):
            open(os.path.join(datasets_path, file), 'w').close()  # Clear file content

def generate_graphs():
    """Generate graphs of means and confidence intervals from the datasets."""
    datasets_path = "dataset"
    output_path = "graphs"
    medias_path = os.path.join(output_path, "averages")
    intervalo_path = os.path.join(output_path, "confidence_interval")
    
    os.makedirs(medias_path, exist_ok=True)
    os.makedirs(intervalo_path, exist_ok=True)

    files = [f for f in os.listdir(datasets_path) if f.endswith(".csv")]

    data = pd.DataFrame()
    for file in files:
        df = pd.read_csv(os.path.join(datasets_path, file))
        df = df.dropna()
        data = pd.concat([data, df], ignore_index=True)

    metrics = ["Throughput (Gbps)", "Mean RTT (ms)", "Packet Loss (%)", "Retransmissions", "CPU Usage (%)"]

    # Define colors and border styles
    ipv4_color = '#6a9fb5'
    ipv6_color = '#f7c07a'
    ipv4_edge = '#3e5f6a'
    ipv6_edge = '#3e5f6a'

    plt.style.use("ggplot")

    # Generate graphs of means
    for metric in metrics:
        metric_data = data.groupby(["TCP Version", "IP Version"])[metric].mean().unstack()
        
        fig, ax = plt.subplots(figsize=(10, 6))
        bars = metric_data.plot(kind="bar", ax=ax, color=[ipv4_color, ipv6_color], 
                                edgecolor=[ipv4_edge, ipv6_edge], linewidth=1.5)

        plt.title(f"Averages of Metric: {metric}")
        plt.ylabel(metric)
        plt.xlabel("TCP Version")
        plt.xticks(rotation=45)

        # Move legend below the plot
        ax.legend(title="IP Version", loc='upper center', bbox_to_anchor=(0.1, -0.10), ncol=2)

        plt.tight_layout()
        plt.savefig(f"{medias_path}/{metric.replace(' ', '_').lower()}.png", dpi=300)
        plt.close()

    # Generate graphs of confidence intervals
    for metric in metrics:
        grouped = data.groupby(["TCP Version", "IP Version"])[metric]
        means = grouped.mean().unstack()
        stds = grouped.std().unstack()
        n = grouped.count().unstack()
        conf_interval = 1.96 * (stds / n**0.5)

        fig, ax = plt.subplots(figsize=(10, 6))
        bars = means.plot(kind="bar", yerr=conf_interval, capsize=4, ax=ax, 
                          color=[ipv4_color, ipv6_color], edgecolor=[ipv4_edge, ipv6_edge], linewidth=1.5)

        plt.title(f"Confidence Interval (95%) of Metric: {metric}")
        plt.ylabel(metric)
        plt.xlabel("TCP Version")
        plt.xticks(rotation=45)

        # Move legend below the plot
        ax.legend(title="IP Version", loc='upper left', bbox_to_anchor=(0, -0.10), ncol=2)

        plt.tight_layout()
        plt.savefig(f"{intervalo_path}/{metric.replace(' ', '_').lower()}_conf.png", dpi=300)
        plt.close()


def generate_table_images():
    """Generate images of tables with dataset values for each TCP version."""
    datasets_path = "dataset"
    output_path = "tables/datasets_tables"
    os.makedirs(output_path, exist_ok=True)

    files = [f for f in os.listdir(datasets_path) if f.endswith(".csv")]

    for file in files:
        df = pd.read_csv(os.path.join(datasets_path, file))

        # Create an image of the dataset table
        fig, ax = plt.subplots(figsize=(12, 6))
        ax.axis('off')
        table = ax.table(cellText=df.values,
                         colLabels=df.columns,
                         loc='center',
                         cellLoc='center')
        table.auto_set_font_size(False)
        table.set_fontsize(8)
        table.scale(1.2, 1.2)

        output_filename = os.path.join(output_path, f"table_{file.replace('.csv', '.png')}")
        plt.title(f"Dataset values: {file}", fontsize=12, pad=10)
        plt.savefig(output_filename, dpi=300, bbox_inches='tight')
        plt.close()

def generate_statistics_tables():
    """Generate statistics tables with mean, median, mode, std dev, and confidence interval."""
    datasets_path = "dataset"
    output_path = "tables/statistics_tables"
    os.makedirs(output_path, exist_ok=True)

    files = [f for f in os.listdir(datasets_path) if f.endswith(".csv")]

    metrics = ["Throughput (Gbps)", "Mean RTT (ms)", "Packet Loss (%)", "Retransmissions", "CPU Usage (%)"]

    for file in files:
        df = pd.read_csv(os.path.join(datasets_path, file))
        tcp_version = file.split("_")[1].capitalize()
        ip_version = file.split("_")[2].split(".")[0].upper()

        stats_data = []

        for metric in metrics:
            mean_value = df[metric].mean()
            median_value = df[metric].median()
            mode_value = df[metric].mode()[0] if not df[metric].mode().empty else "N/A"
            std_dev = df[metric].std()

            # Confidence interval (95%)
            n = len(df[metric])
            if n > 1:
                conf_interval = 1.96 * (std_dev / (n ** 0.5))
                lower_bound = mean_value - conf_interval
                upper_bound = mean_value + conf_interval
            else:
                lower_bound = upper_bound = conf_interval = "N/A"

            stats_data.append([
                metric,
                round(mean_value, 2),
                round(median_value, 2),
                mode_value,
                round(std_dev, 2),
                round(lower_bound, 2),
                round(upper_bound, 2),
                round(conf_interval, 2) if conf_interval != "N/A" else "N/A"
            ])

        # Create a DataFrame for the statistics
        stats_df = pd.DataFrame(stats_data, columns=[
            "Metric", "Average", "Median", "Mode", "Standard Deviation",
            "Lower Limit", "Upper Limit", "Conf. Int. 95%"
        ])

        # Generate and save the table image
        fig, ax = plt.subplots(figsize=(12, 6))
        ax.axis('off')
        table = ax.table(cellText=stats_df.values,
                         colLabels=stats_df.columns,
                         loc='center',
                         cellLoc='center')
        table.auto_set_font_size(False)
        table.set_fontsize(8)
        table.scale(1.2, 1.2)

        output_filename = os.path.join(output_path, f"statistics_{tcp_version}_{ip_version}.png")
        plt.title(f"Descriptive Statistics for TCP {ip_version} with {tcp_version}", fontsize=12, pad=10)
        plt.savefig(output_filename, dpi=300, bbox_inches='tight')
        plt.close()

if __name__ == '__main__':
    setLogLevel('info')

    bw = int(input(Fore.BLUE + "\nEnter the throughput rate (Gbps): " + Style.RESET_ALL)) * 1e3
    loss = int(input(Fore.GREEN + "Enter the data loss percentage (%): " + Style.RESET_ALL))
    delay = int(input(Fore.RED + "Enter the delay (ms): " + Style.RESET_ALL))
    repetitions = int(input(Fore.CYAN + "Enter the number of repetitions: " + Style.RESET_ALL))
    connections = int(input(Fore.MAGENTA + "Enter the number of simultaneous connections: " + Style.RESET_ALL))

    output_log = "output/full_output.log"

    clear_datasets()

    tcp_versions = ['reno', 'cubic', 'bbr', 'vegas', 'veno', 'westwood']
    ip_versions = ['IPv4', 'IPv6']

    total_tests = len(tcp_versions) * len(ip_versions) * repetitions
    current_test = 1

    for tcp_version in tcp_versions:
        for ip_version in ip_versions:
            for test_id in range(1, repetitions + 1):
                print(Fore.YELLOW + f"\nExecuting test {current_test}/{total_tests}..." + Style.RESET_ALL)
                print(f"Starting TCP version: {tcp_version} tests with {ip_version}...")

                net, h1, h2 = create_topology(loss, delay)
                try:
                    measure_metrics(net, h1, h2, f"dataset/dataset_{ip_version.lower()}_{tcp_version.lower()}.csv", output_log, test_id, tcp_version, ip_version)
                finally:
                    cleanup(net)
                current_test += 1

    generate_graphs()
    generate_table_images()
    generate_statistics_tables()
    print(Fore.GREEN + f"\nAll tests completed.")