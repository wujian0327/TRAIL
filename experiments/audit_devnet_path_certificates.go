// Offline replay of archived per-path certificates with the existing Geth BLST
// library. This tool does not modify a client, protocol rule, or live network.
package main

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"

	"github.com/ethereum/go-ethereum/eth/trail"
	blst "github.com/supranational/blst/bindings/go"
)

type record struct {
	TxHash         string   `json:"tx_hash"`
	Epoch          uint64   `json:"epoch"`
	InclusionEpoch uint64   `json:"inclusion_epoch"`
	Path           []uint64 `json:"path"`
	Signature      string   `json:"aggregate_signature_hex"`
	Slot           uint64   `json:"slot"`
}
type relay struct {
	Index   uint64 `json:"validator_index"`
	Address string `json:"relay_address"`
	Pubkey  string `json:"relay_pubkey"`
}

func decode(value string) []byte {
	b, err := hex.DecodeString(strings.TrimPrefix(value, "0x"))
	if err != nil {
		return nil
	}
	return b
}
func number(value uint64) []byte {
	b := make([]byte, 8)
	binary.BigEndian.PutUint64(b, value)
	return b
}
func digest(parts ...[]byte) []byte {
	h := sha256.New()
	for _, p := range parts {
		h.Write(p)
	}
	return h.Sum(nil)
}

func verify(r record, chain uint64, registry map[uint64]relay) bool {
	if len(r.Path) == 0 || len(r.Path) > 16 || r.Epoch > r.InclusionEpoch {
		return false
	}
	tx := decode(r.TxHash)
	if len(tx) != 32 {
		return false
	}
	signature := new(blst.P1Affine).Uncompress(decode(r.Signature))
	if signature == nil {
		return false
	}
	seen := map[uint64]bool{}
	addresses := map[string]bool{}
	identities := [][]byte{}
	keys := []*blst.P2Affine{}
	for _, id := range r.Path {
		entry, ok := registry[id]
		if !ok || seen[id] {
			return false
		}
		seen[id] = true
		address := strings.ToLower(strings.TrimSpace(entry.Address))
		if addresses[address] {
			return false
		}
		addresses[address] = true
		key := new(blst.P2Affine).Uncompress(decode(entry.Pubkey))
		if key == nil {
			return false
		}
		identities = append(identities, digest([]byte(address)))
		keys = append(keys, key)
	}
	domain := []byte(trail.Domain)
	messages := []blst.Message{digest(domain, number(chain), number(r.Epoch), tx, identities[0])}
	publicKeys := []*blst.P2Affine{keys[0]}
	prefix := digest(domain, number(chain), tx, number(r.Epoch), identities[0])
	for i := 0; i < len(keys)-1; i++ {
		message := digest(domain, prefix, identities[i], identities[i+1])
		messages = append(messages, message, message)
		publicKeys = append(publicKeys, keys[i], keys[i+1])
		prefix = digest(prefix, identities[i+1])
	}
	return signature.AggregateVerify(true, publicKeys, true, messages, domain)
}

func main() {
	summaryPath := flag.String("summary", "", "archived summary JSON")
	registryPath := flag.String("registry", "", "registered public keys")
	output := flag.String("output", "", "new audit artifact")
	flag.Parse()
	var s struct {
		Workload struct {
			Chain uint64 `json:"chain_id"`
		} `json:"workload"`
		Blocks struct {
			Records []record `json:"records"`
		} `json:"blocks"`
	}
	var registry struct {
		Relays []relay `json:"relays"`
	}
	read := func(path string, dst any) {
		b, err := os.ReadFile(path)
		if err != nil {
			panic(err)
		}
		if err = json.Unmarshal(b, dst); err != nil {
			panic(err)
		}
	}
	read(*summaryPath, &s)
	read(*registryPath, &registry)
	keys := map[uint64]relay{}
	for _, entry := range registry.Relays {
		keys[entry.Index] = entry
	}
	failed := []int{}
	work := map[uint64]int{}
	counts := map[uint64]int{}
	for index, r := range s.Blocks.Records {
		work[r.Slot] += len(r.Path)
		counts[r.Slot]++
		if !verify(r, s.Workload.Chain, keys) {
			failed = append(failed, index)
		}
	}
	for slot, count := range counts {
		if count > 1024 || work[slot] > 4096 {
			panic("archive exceeds evidence limits")
		}
	}
	if len(s.Blocks.Records) == 0 {
		panic("no archived certificates")
	}
	first := s.Blocks.Records[0]
	if !verify(first, s.Workload.Chain, keys) {
		panic("positive control failed")
	}
	wrong := first
	wrong.Epoch++
	wrong.InclusionEpoch++
	if verify(wrong, s.Workload.Chain, keys) {
		panic("epoch negative control failed")
	}
	wrong = first
	wrong.TxHash = "0x" + strings.Repeat("ab", 32)
	if verify(wrong, s.Workload.Chain, keys) {
		panic("transaction negative control failed")
	}
	result := map[string]any{"passed": len(failed) == 0, "certificates": len(s.Blocks.Records), "failed_record_indices": failed, "epoch_and_transaction_negative_controls_passed": true, "verifier": "Geth blst per-path AggregateVerify; offline replay, no protocol changes"}
	b, _ := json.MarshalIndent(result, "", "  ")
	b = append(b, '\n')
	if err := os.WriteFile(*output, b, 0644); err != nil {
		panic(err)
	}
	fmt.Print(string(b))
	if len(failed) > 0 {
		os.Exit(1)
	}
}
