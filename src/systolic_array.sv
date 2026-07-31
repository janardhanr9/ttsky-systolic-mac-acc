module systolic_array(
    input logic signed [3:0] data_in,
    output logic signed [7:0] final_acc_out,
    input logic clk,
    input logic rst_n,
    input logic signed [3:0] weight_in,
    input logic signed [3:0] bias_in,
    input logic [7:0] pe_acc_en,   
    input logic [7:0] pe_weight_en,
    input logic [7:0] pe_bias_en,    
    input logic [2:0] drain_sel
);

logic signed [3:0] data_chain [0:7];
logic signed [7:0] acc_chain [0:7];

generate 
    genvar i;
    for (i = 0; i < 8; i = i + 1) begin : pe_chain
        systolic_pe pe_inst (
            .data_in      (i == 0 ? data_in : data_chain[i-1]),
            .data_out     (data_chain[i]),
            .clk          (clk),
            .rst_n        (rst_n),
            .weight_in    (weight_in),
            .bias_in      (bias_in),
            .acc_out      (acc_chain[i]),
            .acc_w_en     (pe_acc_en[i]),
            .weight_en    (pe_weight_en[i]),
            .bias_en      (pe_bias_en[i])
        );
    end
endgenerate

always_comb begin
        // Default assignment to avoid any potential latch inference
        final_acc_out = 8'b0;
        case (drain_sel)
            3'b000: final_acc_out = acc_chain[0];
            3'b001: final_acc_out = acc_chain[1];
            3'b010: final_acc_out = acc_chain[2];
            3'b011: final_acc_out = acc_chain[3];
            3'b100: final_acc_out = acc_chain[4];
            3'b101: final_acc_out = acc_chain[5];
            3'b110: final_acc_out = acc_chain[6];
            3'b111: final_acc_out = acc_chain[7];
            default: final_acc_out = 8'b0;
        endcase
    end

endmodule