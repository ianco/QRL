
import sys
import getopt

c_EPOCH_SIZE = 0
c_ROOT_NODE = 'x.x.x.x'

def initialize_parameters(argv):
	global c_EPOCH_SIZE
	global c_ROOT_NODE
	# parse command-line arguments
	try:
		opts, args = getopt.getopt(sys.argv[1:], "mtde:i:", ["epoch=", "ip="])
	except getopt.GetoptError:
		print 'node.py -t/m/d -epoch <size> -ip <ip>'
		sys.exit(2)
	net_type = 't' # default testnet
	def_epoch = '' # epoch size
	def_ip = '' # IP for root node
	for opt, arg in opts:
		if opt == '-h':
			print 'node.py -t/m/d -epoch <size> -ip <ip>'
			sys.exit()
		elif opt == '-t': # testnet
			net_type = 't'
		elif opt == '-m': # mainnet
			print 'Sorry mainnet is not yet supported'
			sys.exit()
		elif opt == '-d': # devnet
			net_type = 'd'
		elif opt in ("-e", "--epoch"):
			def_epoch = arg
		elif opt in ("-i", "--ip"):
			def_ip = arg
	if net_type == 't':
		def_epoch = '10000' # no override allowed for testnet
		def_ip = '104.251.219.40'
		print 'running TESTNET ', def_ip, ' ', def_epoch
	elif net_type == 'd':
		if def_epoch == '' or def_ip == '':
			print 'Error epoch and ip must be specified for local devnet'
			sys.exit(2)
		print 'running local DEVNET ', def_ip, ' ', def_epoch
	c_EPOCH_SIZE = int(def_epoch)
	c_ROOT_NODE = def_ip

def EPOCH_SIZE():
	global c_EPOCH_SIZE
	return c_EPOCH_SIZE

def ROOT_NODE():
	global C_ROOT_NODE
	return c_ROOT_NODE


